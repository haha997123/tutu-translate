import concurrent.futures
import threading
import time
import unittest
from unittest.mock import Mock, patch

import requests

from engine import MAX_TEXT_LENGTH, TranslationError, Translator, _direction
from offline import OfflineTranslationError


class EngineTests(unittest.TestCase):
    def translator(self, **options):
        local = Mock()
        local.translate.return_value = "本地小兔译文"
        local.warmup.return_value = True
        engine = Translator(offline_translator=local, **options)
        engine._google = Mock(return_value="联网小兔译文")
        self.addCleanup(engine.close)
        return engine

    def test_direction_and_compatibility_override(self):
        for text, mode, expected in [("Hello", "auto", ("en", "zh")), ("小兔 bunny", "auto", ("zh", "en")),
                                     ("\U00020000", "auto", ("zh", "en")), ("你好", "en-zh", ("en", "zh"))]:
            self.assertEqual(_direction(text, mode), expected)

    def test_invalid_input_never_calls_network_or_model(self):
        engine = self.translator()
        for text, mode in [(" \n", "auto"), (None, "auto"), ("a" * (MAX_TEXT_LENGTH + 1), "auto"),
                           ("hello", "fr-en"), ("\ud800", "auto")]:
            with self.assertRaises(TranslationError) as caught:
                engine.translate(text, mode)
            self.assertEqual(caught.exception.code, "input")
        engine._google.assert_not_called()
        engine._offline.translate.assert_not_called()

    def test_online_wins_before_grace_and_does_not_run_local(self):
        engine = self.translator()
        result = engine.translate("rabbit")
        self.assertEqual(result["provider"], "Google")
        self.assertEqual(result["text"], "联网小兔译文")
        engine._offline.translate.assert_not_called()

    def test_network_failure_immediately_uses_local_and_cools_down(self):
        engine = self.translator()
        engine._google.side_effect = TranslationError("offline", "network")
        started = time.monotonic()
        for text in ["rabbit", "carrot"]:
            self.assertEqual(engine.translate(text)["provider"], "本地离线")
        self.assertLess(time.monotonic() - started, 0.2)
        engine._google.assert_called_once()
        self.assertEqual(engine._offline.translate.call_count, 2)

    def test_forced_requests_timeout_uses_local(self):
        engine = self.translator()
        engine._google = Translator._google.__get__(engine, Translator)
        session = Mock()
        session.request.side_effect = requests.Timeout("private URL")
        with patch.object(engine, "_session", return_value=session):
            self.assertEqual(engine.translate("hello")["provider"], "本地离线")
        session.request.assert_called_once()

    def test_slow_online_returns_local_after_grace_and_late_result_cannot_replace(self):
        engine = self.translator(online_grace=0.04)
        gate = threading.Event()
        def slow(*args):
            gate.wait(1)
            return "late online"
        engine._google.side_effect = slow
        started = time.monotonic()
        result = engine.translate("rabbit")
        self.assertLess(time.monotonic() - started, 0.2)
        self.assertEqual(result["provider"], "本地离线")
        gate.set()
        time.sleep(0.02)
        self.assertEqual(engine.translate("rabbit")["text"], "本地小兔译文")

    def test_hung_network_work_is_bounded_no_unbounded_queue(self):
        engine = self.translator(online_grace=0.01)
        gate = threading.Event()
        engine._google.side_effect = lambda *args: (gate.wait(2), "late")[1]
        for index in range(8):
            with engine._lock:
                engine._cooldown_until = 0
            self.assertEqual(engine.translate(f"rabbit {index}")["provider"], "本地离线")
        self.assertEqual(engine._google.call_count, 2)
        gate.set()

    def test_explicit_offline_and_warmup_make_zero_network_requests(self):
        engine = self.translator()
        self.assertTrue(engine.warmup()["available"])
        with patch("requests.sessions.Session.request", side_effect=AssertionError("Unexpected network")):
            result = engine.translate_offline("hello")
            self.assertEqual(result["provider"], "本地离线")
        engine._google.assert_not_called()

    def test_offline_override_has_separate_cache(self):
        engine = self.translator()
        self.assertEqual(engine.translate("rabbit")["provider"], "Google")
        self.assertEqual(engine.translate("rabbit", offline=True)["provider"], "本地离线")
        self.assertTrue(engine.translate("rabbit", offline=True)["cached"])

    def test_missing_model_error_is_concise_and_failed_result_not_cached(self):
        engine = self.translator()
        engine._google.side_effect = TranslationError("network", "network")
        engine._offline.translate.side_effect = OfflineTranslationError("本地模型缺失。")
        with self.assertRaises(TranslationError) as caught:
            engine.translate("rabbit")
        self.assertEqual(str(caught.exception), "本地模型缺失。")
        self.assertEqual(caught.exception.code, "offline")
        self.assertFalse(engine._cache)
        self.assertFalse(engine._pending)

    def test_missing_model_can_still_use_slow_online(self):
        engine = self.translator(online_grace=0.01)
        engine._google.side_effect = lambda *args: (time.sleep(0.04), "online")[1]
        engine._offline.translate.side_effect = OfflineTranslationError("model missing")
        self.assertEqual(engine.translate("rabbit")["text"], "online")

    def test_cache_normalizes_newlines_and_returns_independent_dict(self):
        engine = self.translator()
        result = engine.translate(" Rabbit\r\nCarrot ")
        result["text"] = "changed"
        cached = engine.translate("Rabbit\nCarrot")
        self.assertTrue(cached["cached"])
        self.assertEqual(cached["text"], "联网小兔译文")
        engine._google.assert_called_once()

    def test_stale_cache_lru_and_clear(self):
        engine = self.translator(cache_size=2, cache_ttl=10)
        engine.translate("rabbit")
        key = ("rabbit", "en", "zh", False)
        engine._cache[key] = (time.monotonic() - 11, engine._cache[key][1])
        self.assertFalse(engine.translate("rabbit")["cached"])
        engine.translate("carrot")
        engine.translate("apple")
        self.assertEqual(len(engine._cache), 2)
        self.assertNotIn(key, engine._cache)
        engine.clear_cache()
        self.assertFalse(engine._cache)

    def test_concurrent_identical_requests_only_translate_once(self):
        engine = self.translator()
        barrier = threading.Barrier(6)
        engine._google.side_effect = lambda *args: (time.sleep(0.05), "hello")[1]
        def call():
            barrier.wait()
            return engine.translate("rabbit")
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
            results = list(pool.map(lambda _: call(), range(6)))
        engine._google.assert_called_once()
        self.assertEqual(sum(not result["cached"] for result in results), 1)

    def test_google_post_parsing_preserves_paragraphs(self):
        engine = self.translator()
        engine._request = Mock(return_value=[[["小兔。\n\n", "Bunny."], ["胡萝卜。", "Carrot."]]])
        output = Translator._google(engine, "Bunny.\n\nCarrot.", "en", "zh", time.monotonic() + 10)
        self.assertEqual(output, "小兔。\n\n胡萝卜。")
        self.assertEqual(engine._request.call_args.kwargs["data"]["tl"], "zh-CN")
        self.assertEqual(engine._request.call_args.args[0], "POST")

    def test_google_malformed_response_error_never_leaks_body(self):
        engine = self.translator()
        for data in ({"error": "private body"}, [], [None], [[[42]]], [[[None]]]):
            engine._request = Mock(return_value=data)
            with self.assertRaises(TranslationError) as caught:
                Translator._google(engine, "rabbit", "en", "zh", time.monotonic() + 10)
            self.assertNotIn("private", str(caught.exception))

    def test_request_errors_are_sanitized(self):
        engine = self.translator()
        for error, code in [(requests.Timeout("secret URL"), "timeout"),
                            (requests.ConnectionError("private text"), "network"),
                            (requests.RequestException("private text"), "service")]:
            session = Mock()
            session.request.side_effect = error
            with patch.object(engine, "_session", return_value=session):
                with self.assertRaises(TranslationError) as caught:
                    engine._request("POST", "https://example.com", time.monotonic() + 10)
            self.assertEqual(caught.exception.code, code)
            self.assertNotIn("private", str(caught.exception))

    def test_rate_limit_and_expired_deadline(self):
        engine = self.translator()
        session = Mock()
        session.request.return_value.status_code = 429
        with patch.object(engine, "_session", return_value=session):
            with self.assertRaises(TranslationError) as caught:
                engine._request("GET", "https://example.com", time.monotonic() + 10)
            self.assertEqual(caught.exception.code, "quota")
            session.request.reset_mock()
            with self.assertRaises(TranslationError):
                engine._request("GET", "https://example.com", time.monotonic() - 1)
            session.request.assert_not_called()

    def test_warmup_failure_and_close_are_safe(self):
        engine = self.translator()
        engine._offline.warmup.side_effect = OfflineTranslationError("模型缺失")
        self.assertFalse(engine.warmup()["available"])
        engine.close()
        self.assertEqual(engine.translate("rabbit")["provider"], "本地离线")
        engine._google.assert_not_called()


if __name__ == "__main__":
    unittest.main()
