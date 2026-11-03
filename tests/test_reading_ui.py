"""Offline reading UI regression tests; no speaker, helper, HTTP or clipboard."""
from __future__ import annotations

import sys
import unittest
from unittest.mock import Mock

try:
    from .test_app import UIHarness
except ImportError:
    from test_app import UIHarness


def translated(text="hello", source="zh", target="en"):
    return {"text": text, "source": source, "target": target,
            "elapsed": .01, "provider": "Test", "cached": False}


@unittest.skipUnless(sys.platform == "win32", "Delivered application targets Windows")
class ReadingUITests(UIHarness, unittest.TestCase):
    def complete(self, text="hello", source="zh", target="en"):
        self.app._complete(self.app.signature(), translated(text, source, target), None)

    def select(self, editor, first, last):
        editor._textbox.tag_remove("sel", "1.0", "end")
        editor._textbox.tag_add("sel", first, last)

    def test_source_reads_without_translation_and_result_requires_valid_output(self):
        self.app._set_input("hello")
        self.app.read_aloud("result")
        self.reader.speak.assert_not_called()
        self.app.read_aloud("source")
        self.reader.speak.assert_called_once_with("hello", language="en", slow=False)
        self.assertEqual(self.app.read_source_btn.cget("text"), "停止")
        self.assertEqual(self.app.read_result_btn.cget("state"), "disabled")

    def test_chinese_source_uses_chinese_voice(self):
        self.app._set_input("你好，小兔。")
        self.app.read_aloud("source")
        self.reader.speak.assert_called_once_with("你好，小兔。", language="zh", slow=False)

    def test_valid_result_reads_target_language(self):
        self.app._set_input("你好")
        self.complete("Hello rabbit.")
        self.app.read_aloud("result")
        self.reader.speak.assert_called_once_with("Hello rabbit.", language="en", slow=False)
        self.assertEqual(self.app.read_result_btn.cget("text"), "停止")

    def test_source_selection_reads_only_selection(self):
        self.app._set_input("Hello happy rabbit")
        self.select(self.app.source, "1.6", "1.11")
        self.app.read_aloud("source")
        self.reader.speak.assert_called_once_with("happy", language="en", slow=False)

    def test_result_selection_reads_only_selection(self):
        self.app._set_input("快乐的小兔")
        self.complete("Hello happy rabbit")
        self.select(self.app.result, "1.12", "1.18")
        self.app.read_aloud("result")
        self.reader.speak.assert_called_once_with("rabbit", language="en", slow=False)

    def test_slow_flag_and_repeat_click_stops(self):
        self.app._set_input("hello")
        self.app.slow_var.set(True)
        self.app.read_aloud("source")
        self.reader.speak.assert_called_once_with("hello", language="en", slow=True)
        self.app.read_aloud("source")
        self.reader.stop.assert_called_once()
        self.assertIsNone(self.app._speech_id)
        self.assertIsNone(self.app._speech_area)
        self.assertEqual(self.app.read_source_btn.cget("text"), "朗读")

    def test_switching_area_moves_stop_control_and_ignores_old_finish(self):
        self.app._set_input("你好")
        self.complete("hello")
        self.app.read_aloud("source")
        old_id = self.app._speech_id
        self.app.read_aloud("result")
        new_id = self.app._speech_id
        self.app._speech_event({"state": "finished", "id": old_id})
        self.assertEqual(self.app._speech_id, new_id)
        self.assertEqual(self.app._speech_area, "result")
        self.assertEqual(self.app.read_source_btn.cget("text"), "朗读")
        self.assertEqual(self.app.read_result_btn.cget("text"), "停止")
        self.app._speech_event({"state": "finished", "id": new_id})
        self.assertIsNone(self.app._speech_id)
        self.assertEqual(self.app.read_result_btn.cget("text"), "朗读")

    def test_editing_stops_active_reading_and_invalidates_result(self):
        self.app._set_input("你好")
        self.complete("hello")
        self.app.read_aloud("result")
        self.app._set_input("再见")
        self.reader.stop.assert_called_once()
        self.assertIsNone(self.app._speech_id)
        self.assertFalse(self.app._valid_result)
        self.assertEqual(self.app.read_result_btn.cget("state"), "disabled")
        self.assertEqual(self.app.read_result_btn.cget("text"), "朗读")

    def test_speech_error_resets_button_and_displays_message(self):
        self.app._set_input("hello")
        self.app.read_aloud("source")
        self.app._speech_event({"state": "error", "id": self.app._speech_id,
                                "message": "请安装英语语音"})
        self.assertIsNone(self.app._speech_id)
        self.assertEqual(self.app.read_source_btn.cget("text"), "朗读")
        self.assertEqual(self.app.status.cget("text"), "请安装英语语音")

    def test_empty_or_oversized_source_never_starts_reader(self):
        for text in ("", " \n\t", "x" * 5001):
            self.app._set_input(text)
            self.app.read_aloud("source")
        self.reader.speak.assert_not_called()

    def test_source_word_shows_real_ipa_and_word_is_readable(self):
        self.app._set_input("hello")
        label = self.app.phonetic_label.cget("text")
        self.assertIn("hello", label)
        self.assertIn("/", label)
        self.assertEqual(self.app._pronounce_word, "hello")
        self.app.read_aloud("word")
        self.reader.speak.assert_called_once_with("hello", language="en", slow=False)
        self.http.assert_not_called()

    def test_translated_english_word_shows_ipa(self):
        self.app._set_input("兔子")
        self.complete("rabbit")
        self.assertEqual(self.app._pronounce_word, "rabbit")
        self.assertIn("/", self.app.phonetic_label.cget("text"))

    def test_selection_refreshes_ipa(self):
        self.app._set_input("hello happy rabbit")
        self.assertEqual(self.app._pronounce_word, "")
        self.select(self.app.source, "1.12", "1.18")
        self.app._refresh_selection("source")
        self.assertEqual(self.app._pronounce_word, "rabbit")
        self.assertIn("rabbit", self.app.phonetic_label.cget("text"))
        self.assertIn("/", self.app.phonetic_label.cget("text"))

    def test_unknown_word_is_readable_without_fabricated_ipa(self):
        word = "zqxvplmok"
        self.app._set_input(word)
        self.assertEqual(self.app._pronounce_word, word)
        self.assertNotIn("/", self.app.phonetic_label.cget("text"))
        self.assertIn("未收录", self.app.phonetic_label.cget("text"))
        self.assertEqual(self.app.read_word_btn.cget("state"), "normal")
        self.app.read_aloud("word")
        self.reader.speak.assert_called_once_with(word, language="en", slow=False)

    def test_clear_resets_word_result_reading_and_controls(self):
        self.app._set_input("你好")
        self.complete("hello")
        self.app.read_aloud("word")
        self.app.clear()
        self.assertIsNone(self.app._speech_id)
        self.assertEqual(self.app._pronounce_word, "")
        self.assertEqual(self.app.read_word_btn.cget("state"), "disabled")
        self.assertEqual(self.app.read_word_btn.cget("text"), "听单词")
        self.assertEqual(self.app.read_result_btn.cget("state"), "disabled")
        self.assertNotIn("/", self.app.phonetic_label.cget("text"))

    def test_edit_removes_invalidated_translation_ipa(self):
        self.app._set_input("你好")
        self.complete("hello")
        self.app._set_input("再见")
        self.assertFalse(self.app._valid_result)
        self.assertEqual(self.app._pronounce_word, "")
        self.assertEqual(self.app.read_word_btn.cget("state"), "disabled")

    def test_controls_fit_inside_minimum_window_and_do_not_overlap(self):
        self.app.geometry("900x680")
        self.app.deiconify()
        self.app.update()
        width, height = self.app.winfo_width(), self.app.winfo_height()
        controls = [self.app.read_source_btn, self.app.read_result_btn,
                    self.app.read_word_btn, self.app.phonetic_label,
                    self.app.paste_btn, self.app.capture_btn,
                    self.app.service_note, self.app.status]
        for widget in controls:
            with self.subTest(widget=str(widget)):
                x = widget.winfo_rootx() - self.app.winfo_rootx()
                y = widget.winfo_rooty() - self.app.winfo_rooty()
                self.assertGreaterEqual(x, 0)
                self.assertGreaterEqual(y, 0)
                self.assertLessEqual(x + widget.winfo_width(), width)
                self.assertLessEqual(y + widget.winfo_height(), height)
        self.assertGreaterEqual(self.app.source.winfo_height(), 140)
        self.assertGreaterEqual(self.app.result.winfo_height(), 140)
        self.assertLessEqual(self.app.phonetic_label.winfo_rooty() + self.app.phonetic_label.winfo_height(),
                             self.app.capture_btn.winfo_rooty())
        self.http.assert_not_called()

    def test_sidebar_children_fit_inside_minimum_window(self):
        self.app.geometry("900x680")
        self.app.deiconify()
        self.app.update()
        for widget in self.app.sidebar.winfo_children():
            if not widget.winfo_ismapped():
                continue
            with self.subTest(widget=str(widget)):
                self.assertGreaterEqual(widget.winfo_y(), 0)
                self.assertLessEqual(widget.winfo_y() + widget.winfo_height(), self.app.winfo_height())

    def test_shortcut_conflict_warning_fits_inside_minimum_window(self):
        self.app.native = Mock()
        self.app.native.start.return_value = {"tray": False, "hotkey": False}
        self.app._start_native()
        self.app.geometry("900x680")
        self.app.deiconify()
        self.app.update()
        self.assertTrue(self.app.shortcut_note.winfo_ismapped())
        self.assertIn("已被占用", self.app.shortcut_note.cget("text"))
        for widget in self.app.sidebar.winfo_children():
            if not widget.winfo_ismapped():
                continue
            with self.subTest(widget=str(widget)):
                self.assertLessEqual(widget.winfo_y() + widget.winfo_height(), self.app.winfo_height())

    def test_long_ascii_token_does_not_expand_pronunciation_panel(self):
        self.app.geometry("900x680")
        self.app.deiconify()
        self.app._set_input("x" * 5000)
        self.app.update()
        self.assertLessEqual(self.app.phonetic_label.winfo_height(), 100)
        self.assertGreaterEqual(self.app.source.winfo_height(), 140)
        capture_bottom = self.app.capture_btn.winfo_rooty() - self.app.winfo_rooty() + self.app.capture_btn.winfo_height()
        self.assertLessEqual(capture_bottom, self.app.winfo_height())


if __name__ == "__main__":
    unittest.main()
