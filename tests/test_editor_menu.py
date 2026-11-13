"""Real text widgets and menu events with a synthetic clipboard only."""
from __future__ import annotations

import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from tests.test_app import UIHarness


@unittest.skipUnless(sys.platform == "win32", "Windows desktop UI")
class EditorMenuTests(UIHarness, unittest.TestCase):
    def setUp(self):
        self.recorder = Mock(busy=False, recording=False)
        self.recorder.start.return_value = 101
        recorder_patch = patch("app.DictationRecorder", return_value=self.recorder)
        recorder_patch.start()
        self.addCleanup(recorder_patch.stop)
        super().setUp()
        self.controlled_run()
        self.input = self.app.source._textbox
        self.output = self.app.result._textbox
        self.clipboard = "synthetic clipboard"
        self.copied = []
        # Never read or overwrite the computer's actual clipboard.
        for editor in (self.input, self.output):
            for method, replacement in (("clipboard_get", lambda: self.clipboard),
                                        ("clipboard_clear", lambda: None),
                                        ("clipboard_append", self.copied.append)):
                clipboard_patch = patch.object(editor, method, replacement)
                clipboard_patch.start()
                self.addCleanup(clipboard_patch.stop)

    @staticmethod
    def labels(menu):
        return [item["label"] for item in menu.items]

    def open_menu(self, menu=None):
        self.app.deiconify()
        self.app.update()
        menu = menu or self.app.source_menu
        menu.show(SimpleNamespace(x_root=self.input.winfo_rootx() + 30,
                                  y_root=self.input.winfo_rooty() + 30))
        self.app.update()
        self.assertIsNotNone(menu.popup)
        return menu

    def test_only_screenshot_and_voice_actions_remain_in_row(self):
        self.assertFalse(hasattr(self.app, "paste_btn"))
        self.assertEqual(self.app.capture_btn.grid_info()["column"], 0)
        self.assertEqual(self.app.dictation_btn.grid_info()["column"], 1)
        self.assertEqual(self.labels(self.app.source_menu), ["剪切", "复制", "粘贴", "全选"])
        self.assertEqual(self.labels(self.app.result_menu), ["复制", "全选"])

    def test_right_click_on_text_and_placeholder_opens_menu_without_edit(self):
        self.app.deiconify()
        self.app.update()
        menu = self.app.source_menu
        for widget in (self.input, self.app.placeholder._label):
            widget.event_generate("<Button-3>", x=12, y=12)
            self.app.update()
            self.assertIsNotNone(menu.popup)
            self.assertIsNone(self.input.grab_current())
            menu.close()
        self.assertEqual(self.app.text(), "")
        self.assertIsNone(self.app._debounce)
        self.assertEqual(self.runs, [])

    def test_menu_state_tracks_selection_empty_clipboard_and_empty_text(self):
        self.clipboard = ""
        menu = self.app.source_menu
        self.app.source_menu.refresh()
        for label in ("剪切", "复制", "粘贴", "全选"):
            self.assertFalse(menu.enabled(label))
        self.app._set_input("Select me")
        self.input.tag_add("sel", "1.0", "1.6")
        self.clipboard = "New"
        self.app.source_menu.refresh()
        for label in ("剪切", "复制", "粘贴", "全选"):
            self.assertTrue(menu.enabled(label))

    def test_paste_inserts_at_caret_and_schedules_existing_translation(self):
        self.app._set_input("Hello world")
        self.input.mark_set("insert", "1.6")
        self.clipboard = "kind "
        self.app.source_menu.invoke("粘贴")
        self.assertEqual(self.app.text(), "Hello kind world")
        self.assertEqual(self.app.count.cget("text"), "16 / 5000")
        self.assertIsNotNone(self.app._debounce)
        self.assertEqual(self.runs, [])
        self.pump()
        self.assertEqual(self.runs, [("Hello kind world", "auto")])

    def test_paste_replaces_only_selection_and_cancels_current_dictation(self):
        self.app._set_input("one two three")
        self.input.tag_add("sel", "1.4", "1.7")
        self.input.mark_set("insert", "end-1c")
        self.app.toggle_dictation()
        self.clipboard = "NEW"
        self.app.source_menu.invoke("粘贴")
        self.assertEqual(self.app.text(), "one NEW three")
        self.assertIsNone(self.app._dictation_id)
        self.recorder.cancel.assert_called_once()

    def test_copy_preserves_text_and_cut_edits_selected_range(self):
        self.app._set_input("Hello world")
        self.input.tag_add("sel", "1.6", "1.11")
        timer = self.app._debounce
        self.app.source_menu.invoke("复制")
        self.assertEqual(self.copied, ["world"])
        self.assertEqual(self.app.text(), "Hello world")
        self.assertEqual(self.app._debounce, timer)
        self.app.source_menu.invoke("剪切")
        self.assertEqual(self.copied, ["world", "world"])
        self.assertEqual(self.app.text(), "Hello ")
        self.assertNotEqual(self.app._debounce, timer)

    def test_result_menu_copies_selection_but_never_modifies_read_only_text(self):
        self.app._set_result("A translated sentence")
        self.output.tag_add("sel", "1.2", "1.12")
        self.app.result_menu.refresh()
        self.app.result_menu.invoke("复制")
        self.assertEqual(self.copied, ["translated"])
        self.app.result_menu.perform("paste")
        self.app.result_menu.perform("cut")
        self.assertEqual(self.output.get("1.0", "end-1c"), "A translated sentence")
        self.assertEqual(self.output.cget("state"), "disabled")

    def test_all_selects_text_without_triggering_edit(self):
        self.app._set_input("Hello world")
        timer = self.app._debounce
        self.app.source_menu.invoke("全选")
        self.assertEqual(self.input.get("sel.first", "sel.last"), "Hello world")
        self.assertEqual(self.app._debounce, timer)

    def test_native_paste_binding_still_works_with_a_synthetic_tcl_selection(self):
        self.app.deiconify()
        self.app.update()
        self.app._set_input("Hello world")
        self.input.mark_set("insert", "1.6")
        # Tcl's native Text paste reads GetSelection, not the Python method.
        # Replace this interpreter-local reader, leaving the OS clipboard alone.
        self.app.tk.call("rename", "::tk::GetSelection", "::tutu_test_GetSelection")
        self.app.tk.eval('proc ::tk::GetSelection {w {sel PRIMARY}} {return "native "}')
        try:
            self.assertTrue(any("Control-Key-v" in sequence for sequence in self.input.event_info("<<Paste>>")))
            self.input.event_generate("<<Paste>>")
            self.app.update_idletasks()
            self.app._modified()
            self.assertEqual(self.app.text(), "Hello native world")
            self.assertIsNotNone(self.app._debounce)
        finally:
            self.app.tk.call("rename", "::tk::GetSelection", "")
            self.app.tk.call("rename", "::tutu_test_GetSelection", "::tk::GetSelection")

    def test_keyboard_navigation_skips_disabled_rows_and_enter_pastes(self):
        menu = self.open_menu()
        popup = menu.popup
        menu.canvas.event_generate("<Down>")
        self.app.update()
        self.assertEqual(menu.items[menu._active]["action"], "paste")
        menu.canvas.event_generate("<Return>")
        self.app.update_idletasks()
        self.assertEqual(self.app.text(), "synthetic clipboard")
        self.assertIsNone(menu.popup)
        self.assertFalse(popup.winfo_exists())
        self.assertIsNone(self.input.grab_current())

    def test_new_menu_closes_old_menu_and_escape_preserves_text(self):
        self.app._set_input("Keep this text")
        first = self.open_menu().popup
        menu = self.open_menu(self.app.result_menu)
        self.assertFalse(first.winfo_exists())
        self.assertIsNone(self.app.source_menu.popup)
        menu.canvas.event_generate("<Escape>")
        self.app.update()
        self.assertIsNone(menu.popup)
        self.assertEqual(self.app.text(), "Keep this text")

    def test_outside_click_and_window_hide_close_menu_without_grabbing(self):
        menu = self.open_menu()
        self.app.event_generate("<Button-1>", x=4, y=4)
        self.app.update()
        self.assertIsNone(menu.popup)
        menu = self.open_menu()
        self.app.withdraw()
        self.app.update()
        self.assertIsNone(menu.popup)
        self.assertIsNone(self.input.grab_current())

    def test_focus_leaving_popup_closes_it_without_stealing_focus(self):
        menu = self.open_menu()
        self.input.focus_force()
        self.app.update()
        self.assertIsNone(menu.popup)
        self.assertIs(self.app.focus_get(), self.input)

    def test_popup_stays_above_a_pinned_window(self):
        self.app.attributes("-topmost", True)
        menu = self.open_menu()
        self.assertTrue(menu.popup.attributes("-topmost"))
        self.app.event_generate("<Configure>")
        self.app.update()
        self.assertIsNotNone(menu.popup)


if __name__ == "__main__":
    unittest.main()
