"""Desktop UI regressions without HTTP, model loading, speakers or screen grabs."""
from __future__ import annotations

import sys
import threading
import time
import tkinter as tk
import unittest
from unittest.mock import Mock, patch

from app import TutuApp
from engine import TranslationError


def result(text="小兔", source="en", target="zh"):
    return {"text": text, "source": source, "target": target,
            "elapsed": 0.01, "provider": "Test", "cached": False}


class UIHarness:
    """Patch expensive/native services before constructing the real Tk widgets."""
    def setUp(self):
        self.engine = Mock()
        self.engine.warmup.return_value = {"available": True}
        self.engine.translate.return_value = result()
        self.reader = Mock()
        self.reader.speak.side_effect = iter(range(100, 200))
        self.capture = Mock()
        for target, replacement in (("app.Translator", self.engine),
                                    ("app.SpeechReader", self.reader),
                                    ("app.ScreenshotCapture", self.capture)):
            service_patch = patch(target, return_value=replacement)
            service_patch.start()
            self.addCleanup(service_patch.stop)
        http_patch = patch("requests.sessions.Session.request",
                           side_effect=AssertionError("UI tests must never use HTTP"))
        self.http = http_patch.start()
        self.addCleanup(http_patch.stop)
        self.app = TutuApp(native=False)
        self.addCleanup(self._close_ui)
        self.app.withdraw()
        self.app.update_idletasks()

    def _close_ui(self):
        if not self.app._closing:
            # Cancel CTk theme/focus/DPI callbacks before destroying each root.
            for identifier in self.app.tk.call("after", "info"):
                self.app.tk.call("after", "cancel", identifier)
            self.app.quit_app()

    def pump(self, seconds=.65):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            self.app.update()
            time.sleep(.01)
        self.app.update()

    def controlled_run(self):
        self.runs = []
        def run(sig):
            self.runs.append(sig)
            self.app._busy = True
            self.app._active = sig
        self.app._run = run

    def complete(self, text="小兔", source="en", target="zh"):
        self.app._complete(self.app.signature(), result(text, source, target), None)

    def output(self):
        return self.app.result.get("1.0", "end-1c")

    def next_result(self):
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            kind, payload = self.app.events.get(timeout=max(.01, deadline - time.monotonic()))
            if kind == "result":
                return payload
            self.assertEqual(kind, "offline_ready")
        self.fail("Translation worker did not return a result")


@unittest.skipUnless(sys.platform == "win32", "The delivered desktop app targets Windows")
class AppTests(UIHarness, unittest.TestCase):
    def test_input_updates_count_and_invalidates_copyable_result(self):
        self.app._set_input("Rabbit")
        self.complete()
        self.assertTrue(self.app._valid_result)
        self.app._set_input("Carrot")
        self.assertFalse(self.app._valid_result)
        self.assertEqual(self.output(), "")
        self.assertEqual(self.app.copy_btn.cget("state"), "disabled")
        self.assertEqual(self.app.count.cget("text"), "6 / 5000")

    def test_direction_is_always_detected_and_ui_has_no_mode_or_manual_controls(self):
        for text in (" Rabbit ", " 你好 "):
            self.app._set_input(text)
            self.assertEqual(self.app.signature(), (text.strip(), "auto"))
        for name in ("mode", "auto_var", "auto_toggle", "translate_btn", "swap_btn"):
            self.assertFalse(hasattr(self.app, name), name)
        texts = []
        def collect(widget):
            try:
                texts.append(str(widget.cget("text")))
            except (ValueError, tk.TclError):
                # Tk canvas/frames have no text configuration.
                pass
            for child in widget.winfo_children():
                collect(child)
        collect(self.app)
        for forbidden in ("自动识别", "自动翻译", "手动", "中文 → English", "English → 中文", "翻译一下"):
            self.assertFalse(any(forbidden in value for value in texts), forbidden)

    def test_typing_is_debounced_without_needing_a_button(self):
        self.controlled_run()
        self.app._set_input("Rabbit")
        self.assertIsNotNone(self.app._debounce)
        self.pump(.20)
        self.assertEqual(self.runs, [])
        self.pump(.45)
        self.assertEqual(self.runs, [("Rabbit", "auto")])
        self.assertIsNone(self.app._debounce)

    def test_many_edits_reset_timer_and_translate_only_final_text(self):
        self.controlled_run()
        previous = None
        for text in ("R", "Ra", "Rab", "Rabb", "Rabbit"):
            self.app._set_input(text)
            current = self.app._debounce
            self.assertIsNotNone(current)
            self.assertNotEqual(current, previous)
            if previous:
                self.assertNotIn(previous, self.app.tk.call("after", "info"))
            previous = current
        self.assertEqual(self.runs, [])
        self.pump()
        self.assertEqual(self.runs, [("Rabbit", "auto")])

    def test_stale_async_result_is_never_shown(self):
        self.controlled_run()
        self.app._set_input("Rabbit")
        self.app.translate()
        old_sig = self.app._active
        self.app._set_input("Carrot")
        self.app._complete(old_sig, result("过期译文"), None)
        self.assertFalse(self.app._valid_result)
        self.assertEqual(self.output(), "")
        self.assertEqual(len(self.runs), 1)
        self.pump()
        self.assertEqual(self.runs[-1], ("Carrot", "auto"))

    def test_debounced_edit_queues_behind_active_request(self):
        self.controlled_run()
        self.app._set_input("Rabbit")
        self.app.translate()
        old_sig = self.app._active
        self.app._set_input("Carrot")
        self.pump()
        self.assertEqual(self.app._pending, ("Carrot", "auto"))
        self.app._complete(old_sig, result("过期译文"), None)
        self.assertEqual(self.output(), "")
        self.assertEqual(self.runs, [("Rabbit", "auto"), ("Carrot", "auto")])
        self.assertTrue(self.app._busy)

    def test_queued_request_is_cancelled_when_input_changes_again(self):
        self.controlled_run()
        self.app._set_input("Rabbit")
        self.app.translate()
        old_sig = self.app._active
        self.app._set_input("Carrot")
        self.app.translate()
        self.app._set_input("Flower")
        self.assertIsNone(self.app._pending)
        self.app._complete(old_sig, result(), None)
        self.assertEqual(self.runs, [("Rabbit", "auto")])
        self.pump()
        self.assertEqual(self.runs[-1], ("Flower", "auto"))

    def test_editing_back_to_active_text_does_not_submit_duplicate_after_completion(self):
        self.controlled_run()
        self.app._set_input("Rabbit")
        self.app.translate()
        old_sig = self.app._active
        self.app._set_input("Carrot")
        self.app._set_input("Rabbit")
        self.app._complete(old_sig, result(), None)
        self.assertTrue(self.app._valid_result)
        self.pump()
        self.assertEqual(self.runs, [("Rabbit", "auto")])

    def test_repeated_request_does_not_duplicate_active_translation(self):
        self.controlled_run()
        self.app._set_input("Rabbit")
        self.app.translate()
        self.app.translate()
        self.assertEqual(self.runs, [("Rabbit", "auto")])
        self.assertIsNone(self.app._pending)

    def test_ctrl_enter_translates_without_inserting_newline(self):
        self.controlled_run()
        self.app.deiconify()
        self.app.update()
        self.app.source.focus_force()
        self.app._set_input("Rabbit")
        self.app.source._textbox.event_generate("<Control-Return>")
        self.app.update()
        self.assertEqual(self.app.text(), "Rabbit")
        self.assertEqual(self.runs, [("Rabbit", "auto")])
        self.assertIsNone(self.app._debounce)

    def test_empty_input_cancels_debounce_and_in_flight_result(self):
        self.controlled_run()
        self.app._set_input("Rabbit")
        self.app.translate()
        old_sig = self.app._active
        self.app._set_input("Carrot")
        self.app.translate()
        self.app.clear()
        self.assertIsNone(self.app._debounce)
        self.assertIsNone(self.app._pending)
        self.app._complete(old_sig, result(), None)
        self.pump()
        self.assertEqual(self.runs, [("Rabbit", "auto")])
        self.assertEqual(self.app.text(), "")
        self.assertEqual(self.output(), "")
        self.assertFalse(self.app._valid_result)
        self.assertEqual(self.app.count.cget("text"), "0 / 5000")

    def test_empty_and_over_limit_input_never_submit(self):
        self.controlled_run()
        for text in ("", " \n\t", "x" * 5001):
            with self.subTest(length=len(text)):
                self.app._set_input(text)
                self.assertIsNone(self.app._debounce)
                self.app.translate()
                self.assertEqual(self.runs, [])
        self.app._set_input("x" * 5000)
        self.app.translate()
        self.assertEqual(len(self.runs), 1)

    def test_copy_never_touches_clipboard_without_valid_result(self):
        with patch.object(self.app, "clipboard_clear") as clear, \
                patch.object(self.app, "clipboard_append") as append:
            self.app.copy()
            clear.assert_not_called()
            append.assert_not_called()
            self.app._set_input("Rabbit")
            self.complete("小兔")
            self.app.copy()
            clear.assert_called_once()
            append.assert_called_once_with("小兔")
            self.app._set_input("Carrot")
            self.app.copy()
            self.assertEqual(append.call_count, 1)

    def test_oversized_clipboard_is_rejected_without_replacing_input(self):
        self.controlled_run()
        self.app._set_input("Rabbit")
        with patch.object(self.app, "clipboard_get", return_value="x" * 5001), \
                patch.object(self.app, "show"):
            self.app.paste_translate()
        self.assertEqual(self.app.text(), "Rabbit")
        self.assertEqual(self.runs, [])

    def test_worker_handles_failure_and_remains_available_for_next_request(self):
        self.engine.translate.side_effect = [TranslationError("离线测试失败"), result("小兔")]
        self.app._set_input("Rabbit")
        self.app.translate()
        self.app._complete(*self.next_result())
        self.assertFalse(self.app._valid_result)
        self.assertFalse(self.app._busy)
        self.assertEqual(self.app.status.cget("text"), "离线测试失败")
        self.app.translate()
        self.app._complete(*self.next_result())
        self.assertTrue(self.app._valid_result)
        self.assertEqual(self.output(), "小兔")

    def test_worker_reuses_thread_and_never_calls_translation_on_tk_thread(self):
        threads = []
        def fake_translate(*unused):
            threads.append(threading.get_ident())
            return result()
        self.engine.translate.side_effect = fake_translate
        ui_thread = threading.get_ident()
        for text in ("Rabbit", "Carrot"):
            self.app._set_input(text)
            self.app.translate()
            self.app._complete(*self.next_result())
        self.assertEqual(len(set(threads)), 1)
        self.assertNotEqual(threads[0], ui_thread)
        self.engine.warmup.assert_called_once_with()
        self.http.assert_not_called()

    def test_capture_only_starts_when_user_invokes_and_does_not_submit_image(self):
        self.capture.start.assert_not_called()
        self.engine.translate.assert_not_called()
        self.app.capture_btn.invoke()
        self.capture.start.assert_called_once_with()
        self.engine.translate.assert_not_called()
        self.http.assert_not_called()

    def test_ocr_text_is_inserted_and_translated_as_text_only(self):
        self.controlled_run()
        with patch.object(self.app, "show"):
            self.app._ocr_event({"state": "working"})
            self.assertEqual(self.app.capture_btn.cget("state"), "disabled")
            self.app._ocr_event({"state": "done", "text": "A little rabbit.", "error": ""})
        self.assertEqual(self.app.text(), "A little rabbit.")
        self.pump()
        self.assertEqual(self.runs, [("A little rabbit.", "auto")])
        self.assertEqual(self.app.capture_btn.cget("state"), "normal")
        self.http.assert_not_called()

    def test_ocr_returning_same_untranslated_text_still_translates(self):
        self.controlled_run()
        self.app._set_input("Rabbit")
        self.app.capture_translate()
        with patch.object(self.app, "show"):
            self.app._ocr_event({"state": "done", "text": "Rabbit", "error": ""})
        self.pump()
        self.assertEqual(self.runs, [("Rabbit", "auto")])

    def test_cancelled_or_failed_ocr_keeps_existing_input_and_result(self):
        self.controlled_run()
        self.app._set_input("Rabbit")
        self.complete("小兔")
        for payload in ({"state": "cancelled"},
                        {"state": "done", "error": "识别失败", "text": ""},
                        {"state": "done", "error": "", "text": ""}):
            with self.subTest(payload=payload), patch.object(self.app, "show"):
                self.app._ocr_event(payload)
                self.assertEqual(self.app.text(), "Rabbit")
                self.assertEqual(self.output(), "小兔")
                self.assertTrue(self.app._valid_result)
        self.assertEqual(self.runs, [])

    def test_oversized_ocr_cannot_bypass_translation_limit(self):
        self.controlled_run()
        with patch.object(self.app, "show"):
            self.app._ocr_event({"state": "done", "text": "x" * 5001, "error": ""})
        self.pump()
        self.assertEqual(self.runs, [])

    def test_quit_closes_native_services(self):
        for identifier in self.app.tk.call("after", "info"):
            self.app.tk.call("after", "cancel", identifier)
        self.app.quit_app()
        self.engine.close.assert_called_once_with()
        self.reader.close.assert_called_once_with()
        self.capture.close.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
