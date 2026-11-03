"""Local ASR lifecycle tests. Native model/microphone are always replaced."""
from __future__ import annotations

import queue
import sys
import tempfile
import threading
import time
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import numpy as np

import dictation
from dictation import DictationError, DictationRecorder


class NativeModelPathTests(unittest.TestCase):
    @unittest.skipUnless(sys.platform == "win32", "Windows native file encoding")
    def test_token_reader_gets_windows_bytes_and_model_keeps_unicode(self):
        with tempfile.TemporaryDirectory(prefix="tutu-语音-") as temporary:
            root = Path(temporary)
            for name in ("model.int8.onnx", "tokens.txt"):
                (root / name).touch()
            factory = Mock()
            with patch.dict("sys.modules", {"sherpa_onnx": SimpleNamespace(OfflineRecognizer=SimpleNamespace(from_sense_voice=factory))}):
                dictation._load_recognizer(root)
            self.assertEqual(factory.call_args.kwargs["model"], str(root / "model.int8.onnx"))
            self.assertEqual(factory.call_args.kwargs["tokens"], str(root / "tokens.txt").encode("mbcs"))


class FakeInput:
    instances = []
    opening = None
    failure = None

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.started = False
        self.closed = False
        self.stopped = False
        self.instances.append(self)
        if self.opening:
            self.opening.wait(2)
        if self.failure:
            raise self.failure

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True

    def close(self):
        self.closed = True

    def feed(self, count=8000, value=0.2, status=False):
        self.kwargs["callback"](np.full((count, 1), value, dtype=np.float32), count, None, status)


def wait_for(predicate, timeout=2):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return
        time.sleep(0.005)
    raise AssertionError("Worker did not reach expected state")


class DictationTests(unittest.TestCase):
    def setUp(self):
        FakeInput.instances = []
        FakeInput.opening = None
        FakeInput.failure = None
        self.audio_patch = patch.dict("sys.modules", {"sounddevice": SimpleNamespace(InputStream=FakeInput)})
        self.audio_patch.start()
        self.model = Mock()
        self.load_patch = patch("dictation._load_recognizer", return_value=self.model)
        self.load = self.load_patch.start()
        self.decode_patch = patch("dictation._decode", return_value="你好，世界。")
        self.decode = self.decode_patch.start()
        self.events = queue.Queue()
        self.recorder = DictationRecorder(self.events)

    def tearDown(self):
        if FakeInput.opening:
            FakeInput.opening.set()
        self.recorder.close()
        self.decode_patch.stop()
        self.load_patch.stop()
        self.audio_patch.stop()

    def event(self, state, timeout=2):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            try:
                name, payload = self.events.get(timeout=max(0.01, end - time.monotonic()))
            except queue.Empty:
                break
            self.assertEqual(name, "dictation")
            if payload["state"] == state:
                return payload
        self.fail(f"No {state} event")

    def begin(self):
        job = self.recorder.start()
        self.event("recording")
        self.assertTrue(self.recorder.recording)
        return job, FakeInput.instances[-1]

    def finish(self, stream):
        stream.feed()
        self.recorder.stop()
        return self.event("result")

    def test_constructor_never_opens_microphone_or_loads_model(self):
        self.assertEqual(FakeInput.instances, [])
        self.load.assert_not_called()
        self.assertFalse(self.recorder.busy)
        self.assertFalse(self.recorder.recording)

    def test_record_stop_returns_text_and_releases_microphone(self):
        job, stream = self.begin()
        result = self.finish(stream)
        self.assertEqual(result["id"], job)
        self.assertEqual(result["text"], "你好，世界。")
        self.assertTrue(stream.closed)
        self.assertTrue(stream.stopped)
        self.assertFalse(self.recorder.busy)
        self.assertEqual(stream.kwargs["channels"], 1)
        self.assertEqual(stream.kwargs["samplerate"], 16000)
        self.assertEqual(self.recorder._chunks, [])

    def test_repeated_start_while_recording_is_rejected(self):
        self.begin()
        self.assertIsNone(self.recorder.start())
        self.assertEqual(len(FakeInput.instances), 1)

    def test_model_is_lazy_and_reused_across_recordings(self):
        _, first = self.begin()
        self.load.assert_not_called()
        self.finish(first)
        _, second = self.begin()
        self.finish(second)
        self.assertEqual(self.load.call_count, 1)

    def test_cancel_drops_audio_without_decoding(self):
        job, stream = self.begin()
        stream.feed()
        self.recorder.cancel()
        self.assertEqual(self.event("cancelled")["id"], job)
        wait_for(lambda: not self.recorder.busy)
        self.assertTrue(stream.closed)
        self.decode.assert_not_called()
        self.assertEqual(self.recorder._chunks, [])

    def test_cancel_during_decode_blocks_reuse_and_suppresses_result(self):
        entered, release = threading.Event(), threading.Event()
        def delayed(*args):
            entered.set()
            release.wait(2)
            return "stale transcript"
        self.decode.side_effect = delayed
        _, stream = self.begin()
        stream.feed()
        self.recorder.stop()
        self.assertTrue(entered.wait(2))
        self.recorder.cancel()
        self.event("cancelled")
        self.assertTrue(self.recorder.busy)
        self.assertIsNone(self.recorder.start())
        release.set()
        wait_for(lambda: not self.recorder.busy)
        self.assertTrue(self.events.empty())
        self.assertTrue(stream.closed)

    def test_cancel_while_opening_closes_unstarted_stream(self):
        FakeInput.opening = threading.Event()
        self.recorder.start()
        wait_for(lambda: bool(FakeInput.instances))
        self.recorder.cancel()
        FakeInput.opening.set()
        wait_for(lambda: not self.recorder.busy)
        self.assertTrue(FakeInput.instances[0].closed)
        self.assertFalse(FakeInput.instances[0].started)
        self.decode.assert_not_called()

    def test_stop_while_opening_yields_terminal_error(self):
        FakeInput.opening = threading.Event()
        self.recorder.start()
        wait_for(lambda: bool(FakeInput.instances))
        self.recorder.stop()
        FakeInput.opening.set()
        self.assertIn("太短", self.event("error")["message"])
        self.assertFalse(self.recorder.busy)
        self.assertTrue(FakeInput.instances[0].closed)

    def test_no_device_error_does_not_reveal_native_exception(self):
        FakeInput.failure = RuntimeError("C:/private/audio-user-secret")
        self.recorder.start()
        message = self.event("error")["message"]
        self.assertIn("麦克风", message)
        self.assertNotIn("private", message)
        self.assertFalse(self.recorder.busy)

    def test_silence_and_short_recording_skip_model_load(self):
        for count, value, fragment in ((100, 0.2, "太短"), (8000, 0.0, "声音")):
            _, stream = self.begin()
            stream.feed(count, value)
            self.recorder.stop()
            self.assertIn(fragment, self.event("error")["message"])
        self.load.assert_not_called()

    def test_input_overflow_is_reported_and_stream_closed(self):
        _, stream = self.begin()
        stream.feed(status=True)
        self.assertIn("中断", self.event("error")["message"])
        self.assertTrue(stream.closed)
        self.decode.assert_not_called()

    def test_recording_limit_automatically_stops_and_caps_memory(self):
        with patch("dictation.MAX_SECONDS", 1):
            _, stream = self.begin()
            stream.feed(count=30000)
            self.event("result")
            samples = self.decode.call_args.args[1]
            self.assertEqual(len(samples), 16000)
            self.assertTrue(stream.closed)

    def test_stalled_device_is_closed_by_wall_clock_limit(self):
        with patch("dictation.MAX_SECONDS", 0.03):
            _, stream = self.begin()
            self.assertIn("太短", self.event("error")["message"])
            self.assertTrue(stream.closed)
            self.assertFalse(self.recorder.busy)
            self.decode.assert_not_called()

    def test_close_cancels_and_prevents_restart(self):
        _, stream = self.begin()
        self.recorder.close()
        self.assertTrue(stream.closed)
        self.assertFalse(self.recorder.recording)
        self.assertIsNone(self.recorder.start())
        self.decode.assert_not_called()


class AudioValidationTests(unittest.TestCase):
    def test_invalid_samples_fail_before_native_loading(self):
        for samples, rate in ((np.zeros(9000), 16000), (np.ones(1), 16000),
                              (np.ones((8000, 2)), 16000), (np.ones(8000), 0),
                              (np.full(8000, np.nan), 16000)):
            with self.subTest(rate=rate, shape=samples.shape), patch("dictation._load_recognizer") as load:
                with self.assertRaises(DictationError):
                    dictation.recognize_audio(samples, rate)
                load.assert_not_called()

    def test_local_recognizer_config_and_unicode_path(self):
        with tempfile.TemporaryDirectory(prefix="兔兔模型-") as folder:
            model_dir = Path(folder)
            (model_dir / "model.int8.onnx").touch()
            (model_dir / "tokens.txt").touch()
            factory = Mock(return_value="recognizer")
            with patch.dict("sys.modules", {"sherpa_onnx": SimpleNamespace(OfflineRecognizer=SimpleNamespace(from_sense_voice=factory))}):
                self.assertEqual(dictation._load_recognizer(model_dir), "recognizer")
            config = factory.call_args.kwargs
            self.assertEqual(config["language"], "auto")
            self.assertEqual(config["provider"], "cpu")
            self.assertEqual(config["model"], str(model_dir / "model.int8.onnx"))


if __name__ == "__main__":
    unittest.main()
