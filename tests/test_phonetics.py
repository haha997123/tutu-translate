import gzip
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from phonetics import Phonetics, arpabet_to_ipa, normalize_word


class PhoneticsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dictionary = Phonetics()

    def test_common_words_have_real_dictionary_ipa(self):
        expected = {
            "hello": "həˈloʊ", "apple": "ˈæpəl", "computer": "kəmˈpjutɚ",
            "beautiful": "ˈbjutəfəl", "english": "ˈɪŋɡlɪʃ", "cat": "kæt",
        }
        for word, ipa in expected.items():
            with self.subTest(word=word):
                result = self.dictionary.lookup(word)
                self.assertIn(ipa, result["ipa"])
                self.assertEqual(result["accent"], "美式")
                self.assertTrue(result["approximate"])
                self.assertEqual(result["source"], "CMUdict")

    def test_case_typography_selection_and_surrounding_punctuation(self):
        for text in ['  HELLO! ', '“Hello”', '(hello)', "'Hello'", '【hello。】']:
            with self.subTest(text=text):
                self.assertEqual(self.dictionary.lookup(text)["word"], "hello")
        self.assertEqual(self.dictionary.lookup('DON’T')["word"], "don't")
        self.assertEqual(self.dictionary.lookup('x‑ray')["word"], "x-ray")
        self.assertIsNotNone(self.dictionary.lookup("'bout"))

    def test_multiple_readings_are_preserved_and_independent(self):
        result = self.dictionary.lookup("read")
        self.assertEqual(set(result["ipa"]), {"ɹɛd", "ɹid"})
        result["ipa"].clear()
        self.assertEqual(len(self.dictionary.lookup("read")["ipa"]), 2)
        self.assertGreaterEqual(len(self.dictionary.lookup("record")["ipa"]), 2)

    def test_unknown_words_and_phrases_do_not_receive_guessed_ipa(self):
        for text in ["tutuxyzqwerty", "hello world", "你好", "hello\nworld", "",
                     "hello123", "x" * 201, None, 123]:
            with self.subTest(text=text):
                self.assertIsNone(self.dictionary.lookup(text))
        self.assertIsNotNone(self.dictionary.lookup("mother-in-law"))

    def test_stress_and_schwa_conversion(self):
        self.assertEqual(arpabet_to_ipa("HH AH0 L OW1"), "həˈloʊ")
        self.assertEqual(arpabet_to_ipa("T R AE0 N Z L EY1 SH AH0 N"), "tɹænzˈleɪʃən")
        self.assertEqual(arpabet_to_ipa("B ER1 TH D EY2"), "ˈbɝθˌdeɪ")
        self.assertEqual(arpabet_to_ipa("R EH1 K ER0 D"), "ˈɹɛkɚd")
        self.assertEqual(arpabet_to_ipa("K AE1 T"), "kæt")
        for invalid in ["", "K XX1", "GHOST"]:
            with self.assertRaises(ValueError):
                arpabet_to_ipa(invalid)

    def test_full_dictionary_has_broad_coverage(self):
        self.assertGreater(len(self.dictionary._load()), 120000)
        for word in ["biodiversity", "photosynthesis", "algorithm", "pronunciation", "architecture"]:
            self.assertIsNotNone(self.dictionary.lookup(word), word)

    def test_every_bundled_pronunciation_uses_supported_phone_symbols(self):
        for word, variants in self.dictionary._load().items():
            for pronunciation in variants:
                self.assertTrue(arpabet_to_ipa(pronunciation), word)

    def test_loading_is_lazy_thread_safe_and_removes_comments_variants(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            with gzip.open(path / "cmudict.dict.gz", "wt", encoding="utf-8") as target:
                target.write("hello HH AH0 L OW1 # comment\nhello(2) HH EH0 L OW1\nhello(3) HH AH0 L OW1\n")
            dictionary = Phonetics(path)
            self.assertIsNone(dictionary._entries)
            self.assertIsNone(dictionary.lookup("two words"))
            self.assertIsNone(dictionary._entries)
            with ThreadPoolExecutor(max_workers=8) as executor:
                results = list(executor.map(dictionary.lookup, ["hello"] * 20))
            self.assertTrue(all(result["ipa"] == ["həˈloʊ", "hɛˈloʊ"] for result in results))
            self.assertEqual(len(dictionary._entries), 1)

    def test_missing_asset_is_distinct_from_missing_word(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(FileNotFoundError):
                Phonetics(Path(directory)).lookup("hello")


if __name__ == "__main__":
    unittest.main()
