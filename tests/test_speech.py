"""Speech lifecycle tests. They never play sound or send text over a network.

Set TUTU_SPEECH_SELFTEST=1 to additionally synthesize fixed sample text through
the real installed Windows voices into System.Speech's null output.
"""
from __future__ import annotations

import json
import os
import queue
import subprocess
import threading
import time
import unittest
from unittest.mock import Mock, patch

from speech import SpeechReader


class FakeOutput:
    def __init__(self):
        self.lines = queue.Queue()

    def __iter__(self):
        return self

    def __next__(self):
        line = self.lines.get(timeout=5)
        if line is None:
            raise StopIteration
        return line

    def close(self):
        self.lines.put(None)


class FakeInput:
    def __init__(self, process):
        self.process = process

    def write(self, text):
        if self.process.exited:
            raise BrokenPipeError
        command = json.loads(text)
        self.process.commands.append(command)
        action = command["action"]
        if action == "quit":
            self.process.terminate()
        else:
            self.process.emit("speaking" if action == "speak" else "stopped", command["id"])

    def flush(self):
        pass

    def close(self):
        self.process.terminate()


class FakeProcess:
    def __init__(self, ready=True):
        self.stdout = FakeOutput()
        self.stdin = FakeInput(self)
        self.exited = False
        self.commands = []
        if ready:
            self.emit("ready", voices={"en": "Test English", "zh": "Test Chinese"})

    def emit(self, state, request_id=None, **extra):
        self.stdout.lines.put(json.dumps(dict(state=state, id=request_id, message="test", **extra)) + "\n")

    def poll(self):
        return 0 if self.exited else None

    def terminate(self):
        self.exited = True
        self.stdout.close()

    kill = terminate

    def wait(self, timeout=None):
        if not self.exited:
            raise subprocess.TimeoutExpired("fake", timeout)
        return 0


class SpeechTests(unittest.TestCase):
    def setUp(self):
        self.events = queue.Queue()
        self.reader = SpeechReader(self.events)

    def tearDown(self):
        self.reader.close()

    def event(self, state, request_id=None, timeout=2):
        deadline = time.monotonic() + timeout
        seen = []
        while time.monotonic() < deadline:
            try:
                name, payload = self.events.get(timeout=max(0.01, deadline - time.monotonic()))
            except queue.Empty:
                break
            self.assertEqual(name, "speech")
            seen.append(payload)
            if payload["state"] == state and (request_id is None or payload["id"] == request_id):
                return payload
        self.fail("Missing speech event %s; seen %r" % (state, seen))

    def test_constructor_and_empty_input_are_lazy(self):
        with patch.object(self.reader, "_spawn") as spawn:
            self.assertIsNone(self.reader.speak(" \n "))
            self.assertIsNone(self.reader._thread)
            spawn.assert_not_called()

    def test_invalid_language_and_excessive_text_never_start_helper(self):
        with patch.object(self.reader, "_spawn") as spawn:
            self.assertIsNone(self.reader.speak("secret sample", "fr"))
            self.assertNotIn("secret", self.event("error")["message"])
            self.assertIsNone(self.reader.speak("x" * 5001))
            self.assertIn("5000", self.event("error")["message"])
            spawn.assert_not_called()

    def test_failed_start_is_generic_and_retry_is_possible(self):
        fake = FakeProcess()
        with patch.object(self.reader, "_spawn", side_effect=[OSError("private text"), fake]):
            first = self.reader.speak("private text")
            self.assertNotIn("private", self.event("error", first)["message"])
            self.reader._thread.join(timeout=1)
            second = self.reader.speak("another sample")
            self.event("speaking", second)
            self.assertGreater(second, first)

    def test_startup_timeout_releases_helper(self):
        fake = FakeProcess(ready=False)
        self.reader.STARTUP_TIMEOUT = 0.05
        with patch.object(self.reader, "_spawn", return_value=fake):
            token = self.reader.speak("synthetic sample")
            self.event("error", token)
            self.assertTrue(fake.exited)

    def test_json_preserves_quotes_unicode_and_newlines(self):
        fake = FakeProcess()
        text = 'Hello "rabbit".\n小兔 reads $(a); `b`.'
        with patch.object(self.reader, "_spawn", return_value=fake):
            token = self.reader.speak(text, "en-US", slow=True)
            self.event("speaking", token)
            command = fake.commands[-1]
            self.assertEqual(command["text"], text)
            self.assertEqual(command["language"], "en")
            self.assertTrue(command["slow"])

    def test_replaced_prompt_cannot_finish_new_prompt(self):
        fake = FakeProcess()
        with patch.object(self.reader, "_spawn", return_value=fake):
            first = self.reader.speak("first sample")
            self.event("speaking", first)
            second = self.reader.speak("second sample")
            fake.emit("finished", first)
            self.event("speaking", second)
            fake.emit("finished", second)
            self.event("finished", second)
            self.assertTrue(self.events.empty())

    def test_stop_then_speak_filters_old_stop_event(self):
        fake = FakeProcess()
        with patch.object(self.reader, "_spawn", return_value=fake):
            first = self.reader.speak("first sample")
            self.event("speaking", first)
            self.reader.stop()
            second = self.reader.speak("second sample", "zh")
            self.event("speaking", second)
            self.assertGreater(second, first)
            fake.emit("finished", first)
            fake.emit("finished", second)
            self.event("finished", second)
            self.assertTrue(self.events.empty())

    def test_stop_without_use_does_not_start_helper(self):
        self.reader.stop()
        self.event("stopped")
        self.assertIsNone(self.reader._thread)

    def test_close_is_idempotent_and_prevents_more_playback(self):
        fake = FakeProcess()
        with patch.object(self.reader, "_spawn", return_value=fake):
            token = self.reader.speak("sample")
            self.event("speaking", token)
            self.reader.close()
            self.reader.close()
            self.assertTrue(fake.exited)
            self.assertIsNone(self.reader._process)
            self.assertIsNone(self.reader.speak("another sample"))
            self.reader.stop()
            self.assertTrue(self.events.empty())

    @unittest.skipUnless(os.name == "nt", "Windows packaging contract")
    def test_hidden_process_and_no_source_text_in_command_line(self):
        fake = FakeProcess()
        with patch("speech.subprocess.Popen", return_value=fake) as popen:
            self.reader._spawn()
        args, options = popen.call_args
        self.assertIn("-NoProfile", args[0])
        self.assertIn("-NonInteractive", args[0])
        self.assertFalse(options["shell"])
        self.assertEqual(options["creationflags"], subprocess.CREATE_NO_WINDOW)
        self.assertEqual(options["startupinfo"].wShowWindow, subprocess.SW_HIDE)
        self.assertEqual(options["stderr"], subprocess.DEVNULL)
        self.assertTrue(options["text"])
        self.assertNotIn("secret", " ".join(args[0]))


@unittest.skipUnless(os.name == "nt" and os.getenv("TUTU_SPEECH_SELFTEST") == "1",
                     "Opt-in null-output Windows voice integration check")
class RealVoiceTests(unittest.TestCase):
    def test_both_languages_and_rapid_replacement_without_audio(self):
        events = queue.Queue()
        reader = SpeechReader(events, silent=True)
        process = None
        try:
            reader.start()
            deadline = time.monotonic() + 20
            voices = None
            while time.monotonic() < deadline:
                _, payload = events.get(timeout=20)
                self.assertNotEqual(payload["state"], "error", payload)
                if payload["state"] == "ready":
                    voices = payload["voices"]
                    break
            self.assertIn("en", voices)
            self.assertIn("zh", voices)
            for language, text in (("en", "A rabbit reads a book."), ("zh", "小兔正在读书。")):
                token = reader.speak(text, language, slow=True)
                while True:
                    _, payload = events.get(timeout=10)
                    self.assertNotEqual(payload["state"], "error", payload)
                    if payload["state"] == "finished" and payload["id"] == token:
                        break
            old = reader.speak("Rabbit " * 600, "en")
            while True:
                _, payload = events.get(timeout=10)
                if payload["state"] == "speaking" and payload["id"] == old:
                    break
            reader.stop()
            token = reader.speak("Another rabbit.", "en")
            while True:
                _, payload = events.get(timeout=10)
                self.assertNotEqual(payload["state"], "error", payload)
                if payload["state"] == "finished" and payload["id"] == token:
                    break
            process = reader._process
        finally:
            reader.close()
        self.assertIsNotNone(process)
        self.assertIsNotNone(process.poll())
        self.assertIsNone(reader._process)


if __name__ == "__main__":
    unittest.main()
