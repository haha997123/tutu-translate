from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

from offline import MAX_INPUT_TOKENS, MAX_TEXT_LENGTH, OfflineTranslationError, OfflineTranslator, _Model, _chunks


class IdentityTokenizer:
    def encode(self, value, out_type=str):
        return list(value)
    def decode(self, value):
        return "".join(value)


class IdentityRunner:
    def __init__(self):
        self.calls = []
    def translate_batch(self, tokens, **options):
        self.calls.append((tokens, options))
        return [types.SimpleNamespace(hypotheses=[row]) for row in tokens]


class OfflineTests(unittest.TestCase):
    def fake(self):
        local = OfflineTranslator()
        runner = IdentityRunner()
        local._load = Mock(return_value=_Model(runner, IdentityTokenizer(), ""))
        return local, runner

    def test_chunks_preserve_every_character_and_stay_bounded(self):
        for text in ["Hello world. Rabbit!  Carrot? " * 100, "你好。世界！小兔？" * 100, "x" * 3000,
                     "a" * 200 + " " + "b" * 300, "🐰" * 500]:
            pieces = _chunks(text)
            self.assertEqual("".join(pieces), text)
            self.assertTrue(all(0 < len(piece) <= 240 for piece in pieces))

    def test_paragraphs_whitespace_and_unicode_remain(self):
        local, runner = self.fake()
        value = "  Hello world.\n\nRabbit!  \n  胡萝卜。  "
        self.assertEqual(local.translate(value, "en", "zh"), value)
        self.assertTrue(runner.calls)
        self.assertTrue(all(len(row) <= MAX_INPUT_TOKENS for row in runner.calls[0][0]))

    def test_long_inputs_split_without_truncation(self):
        local, runner = self.fake()
        value = "兔" * MAX_TEXT_LENGTH
        self.assertEqual(local.translate(value, "zh", "en").replace(" ", ""), value)
        self.assertGreater(len(runner.calls[0][0]), 1)
        self.assertEqual(sum(len(row) for row in runner.calls[0][0]), len(value))

    def test_real_missing_models_raise_chinese_message_without_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            local = OfflineTranslator(directory)
            with self.assertRaises(OfflineTranslationError) as caught:
                local.translate("hello", "en", "zh")
            self.assertNotIn(directory, str(caught.exception))
            self.assertFalse(local.ready)

    def test_invalid_direction_and_length(self):
        local = OfflineTranslator()
        for source, target in [("fr", "zh"), ("zh", "zh")]:
            with self.assertRaises(OfflineTranslationError):
                local.translate("hello", source, target)
        local, runner = self.fake()
        for text in ["", "  ", None, "x" * (MAX_TEXT_LENGTH + 1)]:
            with self.assertRaises(OfflineTranslationError):
                local.translate(text, "en", "zh")
        self.assertFalse(runner.calls)

    def test_corrupt_metadata_cannot_initialize_wrong_model(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "en_zh"
            root.mkdir()
            for contents in ["{not json}", '{"from_code":"zh","to_code":"en"}']:
                (root / "metadata.json").write_text(contents, encoding="utf-8")
                with self.assertRaises(OfflineTranslationError):
                    OfflineTranslator(directory).translate("hello", "en", "zh")

    def test_unicode_install_path_reads_tokenizer_with_python_not_native_path(self):
        with tempfile.TemporaryDirectory() as directory:
            models = Path(directory) / "兔兔翻译" / "模型"
            root = models / "en_zh"
            (root / "model").mkdir(parents=True)
            (root / "metadata.json").write_text('{"from_code":"en","to_code":"zh"}', encoding="utf-8")
            (root / "model" / "model.bin").write_bytes(b"synthetic weights")
            tokenizer_bytes = b"synthetic tokenizer bytes"
            (root / "sentencepiece.model").write_bytes(tokenizer_bytes)
            native_ct2 = Mock()
            native_sp = Mock()
            with patch.dict("sys.modules", {"ctranslate2": types.SimpleNamespace(Translator=native_ct2),
                                             "sentencepiece": types.SimpleNamespace(SentencePieceProcessor=native_sp)}):
                OfflineTranslator(models)._load("en", "zh")
            native_sp.assert_called_once_with(model_proto=tokenizer_bytes)
            self.assertEqual(native_ct2.call_args.args[0], str(root / "model"))

    def test_runner_failure_is_sanitized(self):
        local, runner = self.fake()
        runner.translate_batch = Mock(side_effect=RuntimeError("private text localpath"))
        with self.assertRaises(OfflineTranslationError) as caught:
            local.translate("hello", "en", "zh")
        self.assertNotIn("private", str(caught.exception))

    def test_local_module_has_no_network_io(self):
        local, runner = self.fake()
        with patch("requests.sessions.Session.request", side_effect=AssertionError("network")):
            self.assertEqual(local.translate("Hello.", "en", "zh"), "Hello.")

    def test_single_english_word_only_collapses_identical_spaced_blocks(self):
        for text, output, expected in [("rabbit", "兔子 兔子", "兔子"),
                                        ("rabbit", "兔子 兔子 兔子", "兔子"),
                                        ("mother", "妈妈", "妈妈"),
                                        ("laugh", "哈哈", "哈哈"),
                                        ("read", "读 阅读", "读 阅读"),
                                        ("rabbit rabbit", "兔子 兔子", "兔子 兔子"),
                                        ("rabbit.", "兔子 兔子", "兔子 兔子")]:
            local, runner = self.fake()
            runner.translate_batch = Mock(return_value=[types.SimpleNamespace(hypotheses=[list(output)])])
            with self.subTest(text=text, output=output):
                self.assertEqual(local.translate(text, "en", "zh"), expected)


if __name__ == "__main__":
    unittest.main()
