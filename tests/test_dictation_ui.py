"""Voice input UI regressions using a fake recorder, without microphone access."""
from __future__ import annotations

import sys
import unittest
from unittest.mock import Mock, patch

from tests.test_app import UIHarness


@unittest.skipUnless(sys.platform == "win32", "Windows desktop UI")
class DictationUITests(UIHarness, unittest.TestCase):
    def setUp(self):
        self.recorder = Mock(recording=False, busy=False)
        self.recorder.start.side_effect = iter(range(1, 20))
        service_patch = patch("app.DictationRecorder", return_value=self.recorder)
        service_patch.start()
        self.addCleanup(service_patch.stop)
        super().setUp()
        self.controlled_run()

    def start_recording(self):
        self.app.toggle_dictation()
        identifier = self.app._dictation_id
        self.app._dictation_event({"id": identifier, "state": "recording"})
        return identifier

    def deliver(self, identifier, text="Good morning"):
        self.app._dictation_event({"id": identifier, "state": "result", "text": text})

    def test_stop_then_result_replaces_old_text_and_uses_existing_debounce(self):
        self.app._set_input("Old text")
        identifier = self.start_recording()
        self.assertEqual(self.app.text(), "Old text")
        self.assertEqual(self.app.dictation_btn.cget("text"), "结束录音")
        self.reader.stop.assert_called()
        self.app.toggle_dictation()
        self.recorder.stop.assert_called_once()
        self.assertEqual(self.app.dictation_btn.cget("state"), "disabled")
        self.deliver(identifier)
        self.assertEqual(self.app.text(), "Good morning")
        self.assertIsNotNone(self.app._debounce)
        self.assertIsNone(self.app._dictation_id)
        self.pump()
        self.assertEqual(self.runs, [("Good morning", "auto")])

    def test_edit_even_if_reverted_cancels_and_preserves_input(self):
        self.app._set_input("Original")
        identifier = self.start_recording()
        self.app._set_input("An edit")
        self.app._set_input("Original")
        self.deliver(identifier, "Stale speech")
        self.assertEqual(self.app.text(), "Original")
        self.recorder.cancel.assert_called_once()

    def test_result_from_older_job_cannot_overwrite_new_job(self):
        old = self.start_recording()
        self.app.cancel_dictation()
        new = self.start_recording()
        self.deliver(old, "Old")
        self.assertEqual(self.app.text(), "")
        self.assertEqual(self.app._dictation_id, new)
        self.deliver(new, "New")
        self.assertEqual(self.app.text(), "New")

    def test_late_recording_event_after_stop_does_not_enable_button(self):
        identifier = self.start_recording()
        self.app.toggle_dictation()
        self.app._dictation_event({"id": identifier, "state": "recording"})
        self.assertEqual(self.app.dictation_btn.cget("text"), "识别中…")
        self.assertEqual(self.app.dictation_btn.cget("state"), "disabled")

    def test_clear_paste_capture_hide_and_page_switch_cancel(self):
        actions = (
            self.app.clear,
            self.app.paste_translate,
            self.app.capture_translate,
            lambda: self.app.show_page("learning"),
        )
        self.app.clipboard_clear()
        self.app.clipboard_append("Pasted")
        for action in actions:
            self.app.show_page("translate")
            identifier = self.start_recording()
            action()
            preserved = self.app.text()
            self.deliver(identifier, "Should be ignored")
            self.assertEqual(self.app.text(), preserved)
            self.assertIsNone(self.app._dictation_id)
        self.app.show_page("translate")
        identifier = self.start_recording()
        self.app.native_status["tray"] = True
        self.app.hide()
        self.deliver(identifier, "Should be ignored")
        self.assertIsNone(self.app._dictation_id)
        self.assertGreaterEqual(self.recorder.cancel.call_count, 5)

    def test_reading_cancels_recording_before_speech(self):
        self.app._set_input("Hello")
        self.start_recording()
        self.app.read_aloud("source")
        self.assertIsNone(self.app._dictation_id)
        self.recorder.cancel.assert_called_once()
        self.reader.speak.assert_called_once()

    def test_error_keeps_original_text_and_resets_button(self):
        self.app._set_input("Keep me")
        identifier = self.start_recording()
        self.app._dictation_event({"id": identifier, "state": "error", "message": "麦克风不可用"})
        self.assertEqual(self.app.text(), "Keep me")
        self.assertEqual(self.app.dictation_btn.cget("text"), "语音输入")
        self.assertEqual(self.app.status.cget("text"), "麦克风不可用")

    def test_busy_or_missing_id_and_start_exception_leave_input_intact(self):
        self.app._set_input("Keep me")
        self.recorder.busy = True
        self.app.toggle_dictation()
        self.recorder.start.assert_not_called()
        self.recorder.busy = False
        self.recorder.start.side_effect = (None, RuntimeError("private path"))
        self.app.toggle_dictation()
        self.assertIsNone(self.app._dictation_id)
        self.app.toggle_dictation()
        self.assertIsNone(self.app._dictation_id)
        self.assertEqual(self.app.text(), "Keep me")
        self.assertNotIn("private path", self.app.status.cget("text"))

    def test_shortcut_only_starts_on_translate_page(self):
        self.assertEqual(self.app._dictation_key(None), "break")
        self.recorder.start.assert_called_once()
        self.app.show_page("learning")
        self.app._dictation_key(None)
        self.recorder.start.assert_called_once()

    def test_quit_closes_recorder(self):
        self.start_recording()
        self._close_ui()
        self.recorder.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
