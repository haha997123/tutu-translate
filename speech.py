"""Nonblocking, offline Windows speech through the installed SAPI voices.

The hidden helper receives JSON on stdin, keeps one synthesizer alive, and exits
when stdin closes. Source text is never written to a file, command line, or log.
All UI notifications are queue events; no Tk methods run on these threads.
"""
from __future__ import annotations

import atexit
import itertools
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time


class SpeechReader:
    """Read English/Chinese with the Windows voices already on this computer.

    ``silent=True`` is an explicit diagnostic hook: synthesize to a null output
    rather than a speaker. It must only be used by tests with synthetic text.
    ``speak`` returns an integer request ID, or None for an empty/invalid request.
    Only the most recent speak/stop command can emit a playback state event.
    """

    STARTUP_TIMEOUT = 18.0
    MAX_CHARS = 5000

    def __init__(self, callback_queue, assets_path=None, *, silent=False):
        self.events = callback_queue
        root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
        self.assets = Path(assets_path) if assets_path is not None else root / "assets"
        self._silent = bool(silent)
        self._lock = threading.RLock()
        self._commands = queue.Queue()
        self._ids = itertools.count(1)
        self._latest_id = None
        self._thread = None
        self._process = None
        self._closed = False
        self.voices = {}
        atexit.register(self.close)

    def _emit(self, state, request_id=None, message="", **extra):
        with self._lock:
            if self._closed:
                return
        payload = {"state": state, "id": request_id, "message": message}
        payload.update(extra)
        self.events.put(("speech", payload))

    def start(self):
        """Start the helper without blocking the calling/UI thread; safe to repeat."""
        with self._lock:
            if self._closed or (self._thread and self._thread.is_alive()):
                return
            self._thread = threading.Thread(target=self._run, name="tutu-speech", daemon=True)
            self._thread.start()

    def speak(self, text, language="en", slow=False):
        text = str(text or "").strip()
        if not text:
            return None
        with self._lock:
            if self._closed:
                return None
            request_id = next(self._ids)
            self._latest_id = request_id
            lang = str(language).lower().split("-")[0]
            if lang not in {"en", "zh"}:
                self._emit("error", request_id, "暂时只支持中文和英文朗读。")
                return None
            if len(text) > self.MAX_CHARS:
                self._emit("error", request_id, "朗读文字最多 5000 个字符。")
                return None
            self._commands.put({"action": "speak", "id": request_id,
                                "text": text, "language": lang, "slow": bool(slow)})
            self.start()
            return request_id

    def stop(self):
        """Stop current/pending playback without starting an unused helper."""
        with self._lock:
            if self._closed:
                return
            request_id = next(self._ids)
            self._latest_id = request_id
            if self._thread and self._thread.is_alive():
                self._commands.put({"action": "stop", "id": request_id})
            else:
                self._emit("stopped", request_id, "朗读已停止")

    def close(self):
        """Release local audio and the helper, with a bounded shutdown wait."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._commands.put({"action": "quit"})
            thread = self._thread
        if thread and thread is not threading.current_thread():
            thread.join(timeout=2.5)
        # A driver or COM initialization can hang: do not leave a helper behind.
        with self._lock:
            process = self._process
        self._terminate(process)
        if thread and thread is not threading.current_thread():
            thread.join(timeout=0.5)
        try:
            atexit.unregister(self.close)
        except Exception:
            pass

    @staticmethod
    def _terminate(process):
        if process is None:
            return
        try:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=1.0)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=1.0)
        except (OSError, subprocess.TimeoutExpired):
            pass
        for stream in (process.stdin, process.stdout):
            try:
                if stream:
                    stream.close()
            except (OSError, ValueError):
                pass

    def _spawn(self):
        if os.name != "nt":
            raise OSError("Windows speech is unavailable")
        worker = self.assets / "speech_worker.ps1"
        powershell = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32/WindowsPowerShell/v1.0/powershell.exe"
        if not worker.is_file() or not powershell.is_file():
            raise OSError("Windows speech component is unavailable")
        args = [str(powershell), "-NoLogo", "-NoProfile", "-NonInteractive",
                "-ExecutionPolicy", "Bypass", "-File", str(worker)]
        if self._silent:
            args.append("-Silent")
        startup = subprocess.STARTUPINFO()
        startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startup.wShowWindow = subprocess.SW_HIDE
        return subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, text=True, encoding="utf-8",
                                errors="replace", bufsize=1, shell=False,
                                creationflags=subprocess.CREATE_NO_WINDOW,
                                startupinfo=startup)

    @staticmethod
    def _read_output(process, inbox):
        try:
            for line in process.stdout:
                try:
                    payload = json.loads(line)
                    if isinstance(payload, dict):
                        inbox.put(payload)
                except (ValueError, TypeError):
                    # Do not log unexpected output: a helper must not expose text.
                    continue
        except (OSError, ValueError):
            pass
        finally:
            inbox.put({"_eof": True})

    def _run(self):
        process = None
        inbox = queue.Queue()
        ready = False
        failed = False
        try:
            process = self._spawn()
            with self._lock:
                self._process = process
            threading.Thread(target=self._read_output, args=(process, inbox),
                             name="tutu-speech-output", daemon=True).start()
            deadline = time.monotonic() + self.STARTUP_TIMEOUT
            while True:
                with self._lock:
                    if self._closed:
                        break
                # Process stdout before stdin so startup and failures are known.
                while True:
                    try:
                        payload = inbox.get_nowait()
                    except queue.Empty:
                        break
                    if payload.get("_eof"):
                        raise OSError("Speech helper exited")
                    state = payload.get("state")
                    if state == "ready":
                        ready = True
                        self.voices = payload.get("voices", {})
                        self._emit("ready", None, "离线朗读已就绪", voices=self.voices)
                    elif state in {"speaking", "stopped", "finished", "error"}:
                        with self._lock:
                            current = self._latest_id
                        # The old prompt may finish after a replacement/stop.
                        if payload.get("id") == current:
                            self._emit(state, current, payload.get("message", ""))
                        elif state == "error" and payload.get("id") is None:
                            raise OSError("Speech initialization failed")
                if not ready and time.monotonic() > deadline:
                    raise TimeoutError("Speech startup timed out")
                if ready:
                    try:
                        command = self._commands.get(timeout=0.04)
                    except queue.Empty:
                        continue
                    # Coalesce clicks made during startup/previous processing.
                    while True:
                        try:
                            newer = self._commands.get_nowait()
                            command = newer
                        except queue.Empty:
                            break
                    if command["action"] == "quit":
                        break
                    process.stdin.write(json.dumps(command, ensure_ascii=False) + "\n")
                    process.stdin.flush()
                else:
                    time.sleep(0.025)
        except (OSError, ValueError, TimeoutError, subprocess.SubprocessError):
            failed = True
        finally:
            if process is not None:
                try:
                    if process.poll() is None:
                        process.stdin.write('{"action":"quit"}\n')
                        process.stdin.flush()
                        process.stdin.close()
                        process.wait(timeout=0.8)
                except (OSError, ValueError, subprocess.TimeoutExpired):
                    pass
                self._terminate(process)
            with self._lock:
                if self._process is process:
                    self._process = None
                closed = self._closed
                current = self._latest_id
                # A failed command must not replay after a later explicit retry.
                if failed:
                    while not self._commands.empty():
                        try:
                            self._commands.get_nowait()
                        except queue.Empty:
                            break
            if failed and not closed:
                self._emit("error", current, "无法启动离线朗读，请检查 Windows 语音组件。")


def self_test(assets_path, report_path):
    """Verify packaged voices using fixed samples and **no audible playback**.

    The optional report contains voice names, states, and timings only. No user
    text is accepted by this diagnostic. Each startup/sample wait is bounded.
    """
    events = queue.Queue()
    reader = SpeechReader(events, assets_path, silent=True)
    report = {"ok": False, "silent": True, "voices": {}, "samples": []}
    process = None

    def wait_for(state, token=None, timeout=20):
        deadline = time.monotonic() + timeout
        states = []
        while time.monotonic() < deadline:
            _, payload = events.get(timeout=max(0.01, deadline - time.monotonic()))
            if payload.get("state") == "error":
                raise RuntimeError("Offline speech self-test failed")
            if token is not None and payload.get("id") != token:
                continue
            states.append(payload.get("state"))
            if payload.get("state") == state:
                return payload, states
        raise TimeoutError("Offline speech self-test timed out")

    try:
        started = time.monotonic()
        reader.start()
        ready, _ = wait_for("ready")
        report["startup_seconds"] = round(time.monotonic() - started, 3)
        report["voices"] = ready.get("voices", {})
        if not all(language in report["voices"] for language in ("en", "zh")):
            raise RuntimeError("Required voice is unavailable")
        for language, text in (("en", "A rabbit reads a book."), ("zh", "小兔正在读书。")):
            started = time.monotonic()
            token = reader.speak(text, language, slow=False)
            _, states = wait_for("finished", token)
            if "speaking" not in states:
                raise RuntimeError("Missing speaking notification")
            report["samples"].append({"language": language, "states": states,
                                      "seconds": round(time.monotonic() - started, 3)})
        report["ok"] = True
    except (OSError, RuntimeError, TimeoutError, queue.Empty):
        report["error"] = "Windows 离线语音检查未通过，请检查中文、英文语音包及音频组件。"
    finally:
        process = reader._process
        closed = time.monotonic()
        reader.close()
        report["close_seconds"] = round(time.monotonic() - closed, 3)
        report["helper_closed"] = process is None or process.poll() is not None
        report["ok"] = report["ok"] and report["helper_closed"]
    if report_path is not None:
        path = Path(report_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report
