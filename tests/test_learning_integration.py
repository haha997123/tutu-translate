"""Integration checks for the real learning page, without HTTP or speakers."""
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

from test_app import UIHarness


class LearningIntegrationTests(UIHarness, unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        super().setUp()
        self.app._learning_state_path = Path(self.temp.name) / 'progress.json'

    def open_learning(self):
        self.app.learning_nav.invoke()
        self.pump(.15)
        self.assertEqual(self.app._current_page, 'learning')
        self.assertIsNotNone(self.app.learning_page)
        return self.app.learning_page

    def test_learning_buttons_keep_translation_separate_and_save_progress(self):
        page = self.open_learning()
        self.assertEqual(page.plan['counts']['word_total'], 10)
        self.assertEqual(page.plan['counts']['sentence_total'], 5)
        english = page._current['english']
        page.slow_var.set(True)
        page.read_btn.invoke()
        self.reader.speak.assert_called_with(english, language='en', slow=True)
        speech_id = self.app._speech_id
        self.assertEqual(page.read_btn.cget('text'), '停止朗读')
        self.app._speech_event({'id': speech_id, 'state': 'finished'})
        self.assertEqual(page.read_btn.cget('text'), '朗读英语')
        learned_id = page._current['id']
        page.known_btn.invoke()
        self.assertIn(learned_id, page.plan['done_ids'])
        self.assertNotEqual(page._current['id'], learned_id)
        self.assertTrue(self.app._learning_state_path.is_file())
        self.http.assert_not_called()
        self.app.translate_nav.invoke()
        self.assertEqual(self.app._current_page, 'translate')
        self.app.learning_nav.invoke()
        self.assertIn(learned_id, page.plan['done_ids'])

    def test_settings_sentence_reading_and_failed_input(self):
        page = self.open_learning()
        page.word_goal.set('3')
        page.sentence_goal.set('2')
        page.save_btn.invoke()
        self.assertEqual(page.plan['counts']['word_total'], 3)
        self.assertEqual(page.plan['counts']['sentence_total'], 2)
        page._change_kind('今日句子')
        page.read_btn.invoke()
        self.reader.speak.assert_called_with(page._current['english'], language='en', slow=False)
        page.word_goal.set('no')
        page.save_btn.invoke()
        self.assertEqual(self.app.learning_store.get_settings()['word_count'], 3)
        self.assertIn('整数', page.status_label.cget('text'))

    def test_translation_shortcuts_and_ocr_return_to_translate(self):
        self.open_learning()
        with patch.object(self.app, 'clipboard_get', return_value='test paste'):
            self.app.paste_translate()
        self.assertEqual(self.app._current_page, 'translate')
        self.assertEqual(self.app.text(), 'test paste')
        self.app.show_page('learning')
        self.app._ocr_event({'state':'done','text':'captured text','error':None})
        self.assertEqual(self.app._current_page, 'translate')
        self.assertEqual(self.app.text(), 'captured text')

    def test_learning_controls_fit_minimum_window(self):
        page = self.open_learning()
        self.app.deiconify()
        self.app.geometry('900x680')
        self.pump(.15)
        right = self.app.winfo_rootx() + self.app.winfo_width()
        bottom = self.app.winfo_rooty() + self.app.winfo_height()
        for widget in (self.app.translate_nav,self.app.learning_nav,page.save_btn,page.read_btn,
                       page.known_btn,page.again_btn,page.prev_btn,page.next_btn,page.status_label):
            self.assertLessEqual(widget.winfo_rootx()+widget.winfo_width(), right)
            self.assertLessEqual(widget.winfo_rooty()+widget.winfo_height(), bottom)
        self.assertGreater(page.content._parent_canvas.winfo_height(),100)


if __name__ == '__main__':
    unittest.main()
