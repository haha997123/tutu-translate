"""Explicit microphone input through the published local SenseVoice model.

Audio stays in bounded memory and is discarded after recognition/cancellation.
No microphone is opened and no native model is loaded by the constructor.
"""
from __future__ import annotations

import atexit
import itertools
import os
from pathlib import Path
import sys
import threading


SAMPLE_RATE = 16000
MAX_SECONDS = 60


class DictationError(Exception):
    """A display-safe message, containing no transcript or private file paths."""


def default_model_path() -> Path:
    root = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
    return root / "models" / "asr" / "sensevoice"


def _native_token_path(path):
    # sherpa's token reader uses narrow std::ifstream on Windows. pybind11
    # accepts bytes without re-encoding them to UTF-8; pass the Windows ANSI
    # encoding that this reader actually expects. ONNX Runtime's model reader
    # separately supports the Unicode str path and must keep receiving that.
    if sys.platform != "win32":
        return str(path)
    try:
        return str(path).encode("mbcs")
    except UnicodeEncodeError as exc:
        raise DictationError("语音模型目录含有系统不支持的字符，请把软件移到英文目录后重试。") from exc


def _load_recognizer(model_dir):
    model_dir = Path(model_dir)
    if not all((model_dir / name).is_file() for name in ("model.int8.onnx", "tokens.txt")):
        raise DictationError("缺少本地语音模型，请重新安装完整的兔兔翻译。")
    token_path = _native_token_path(model_dir / "tokens.txt")
    try:
        import sherpa_onnx
        return sherpa_onnx.OfflineRecognizer.from_sense_voice(
            model=str(model_dir / "model.int8.onnx"),
            tokens=token_path,
            num_threads=min(4, max(1, os.cpu_count() or 1)),
            use_itn=True, language="auto", provider="cpu", debug=False,
        )
    except Exception as exc:
        raise DictationError("本地语音模型未能载入，请重新安装完整版本。") from exc


def _validate_audio(samples, sample_rate):
    import numpy as np
    if not isinstance(sample_rate, int) or sample_rate < 8000 or sample_rate > 192000:
        raise DictationError("这段录音的采样率不受支持。")
    samples = np.asarray(samples, dtype=np.float32)
    if samples.ndim != 1 or len(samples) > sample_rate * MAX_SECONDS:
        raise DictationError("录音需为单声道，且不超过 60 秒。")
    if len(samples) < sample_rate // 4:
        raise DictationError("录音太短了，请再说一句。")
    if not np.isfinite(samples).all():
        raise DictationError("录音数据异常，请重新录音。")
    if float(np.max(np.abs(samples))) < 0.003 or float(np.sqrt(np.mean(samples * samples))) < 0.0003:
        raise DictationError("没有听清声音，请靠近麦克风再试。")
    return np.ascontiguousarray(samples)


def _decode(recognizer, samples, sample_rate):
    try:
        stream = recognizer.create_stream()
        stream.accept_waveform(sample_rate, samples)
        recognizer.decode_stream(stream)
        text = stream.result.text.strip()
    except Exception as exc:
        raise DictationError("本地语音识别失败，请重新录音。") from exc
    if not text:
        raise DictationError("这次没有识别出文字，请说清楚一些再试。")
    return text


def recognize_audio(samples, sample_rate=16000, model_dir=None):
    """Recognize an explicit, mono in-memory sample (also used by sample QA)."""
    samples = _validate_audio(samples, sample_rate)
    return _decode(_load_recognizer(model_dir or default_model_path()), samples, sample_rate)


class DictationRecorder:
    """One asynchronous recording/decode at a time, using local CPU inference.

    ``start`` returns an integer job ID, or None when closed/busy. Events have
    the shape ``('dictation', {'id': id, 'state': ..., 'message': ..., 'text': ...})``.
    Cancellation suppresses late results, including an already running native
    decode. ``busy`` remains true until that worker releases its resources.
    """

    def __init__(self, event_queue, model_dir=None):
        self.events = event_queue
        self.model_dir = Path(model_dir) if model_dir is not None else default_model_path()
        self._lock = threading.RLock()
        self._ids = itertools.count(1)
        self._id = None
        self._closed = False
        self._busy = False
        self._recording = False
        self._cancelled = False
        self._stop = threading.Event()
        self._thread = None
        self._recognizer = None
        self._chunks = []
        self._sample_count = 0
        self._capture_error = False
        atexit.register(self.close)

    @property
    def recording(self):
        with self._lock:
            return self._recording

    @property
    def busy(self):
        with self._lock:
            return self._busy

    def _emit(self, job_id, state, message="", text=""):
        with self._lock:
            if self._closed or self._id != job_id or (self._cancelled and state != "cancelled"):
                return
            self.events.put(("dictation", {"id": job_id, "state": state, "message": message, "text": text}))

    def start(self):
        with self._lock:
            if self._closed or self._busy:
                return None
            job_id = next(self._ids)
            self._id = job_id
            self._busy = True
            self._recording = False
            self._cancelled = False
            self._chunks = []
            self._sample_count = 0
            self._capture_error = False
            self._stop = threading.Event()
            self._thread = threading.Thread(target=self._run, args=(job_id,), name="tutu-dictation", daemon=True)
            self._emit(job_id, "starting", "正在开启麦克风…")
            self._thread.start()
            return job_id

    def stop(self):
        with self._lock:
            if self._busy:
                self._recording = False
                self._stop.set()

    def cancel(self):
        with self._lock:
            if not self._busy or self._cancelled:
                return
            self._cancelled = True
            self._recording = False
            self._chunks.clear()
            self._stop.set()
            self._emit(self._id, "cancelled", "语音输入已取消。")

    def close(self):
        with self._lock:
            if self._closed:
                return
            self.cancel()
            self._closed = True
            self._recognizer = None
            thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=1.0)
        try:
            atexit.unregister(self.close)
        except Exception:
            pass

    def _callback(self, job_id, data, frames, timing, status):
        with self._lock:
            if self._closed or self._cancelled or self._id != job_id or self._stop.is_set():
                return
            if status:
                # A dropped audio buffer can corrupt the result; ask for a retry.
                self._capture_error = True
                self._stop.set()
                return
            remaining = SAMPLE_RATE * MAX_SECONDS - self._sample_count
            chunk = data[:remaining, 0].copy()
            self._chunks.append(chunk)
            self._sample_count += len(chunk)
            if self._sample_count >= SAMPLE_RATE * MAX_SECONDS:
                self._recording = False
                self._stop.set()

    def _run(self, job_id):
        stream = None
        try:
            try:
                import sounddevice as sd
                with self._lock:
                    if self._cancelled or self._closed:
                        return
                stream = sd.InputStream(
                    samplerate=SAMPLE_RATE, channels=1, dtype="float32", blocksize=1600,
                    callback=lambda data, frames, timing, status: self._callback(job_id, data, frames, timing, status),
                )
                with self._lock:
                    if self._cancelled or self._closed:
                        return
                    if self._stop.is_set():
                        raise DictationError("录音太短了，请再说一句。")
                # Native driver startup must not hold the lifecycle lock:
                # callbacks and a UI cancellation need to acquire it too.
                stream.start()
                with self._lock:
                    if self._cancelled or self._closed:
                        return
                    if self._stop.is_set():
                        raise DictationError("录音太短了，请再说一句。")
                    self._recording = True
                    self._emit(job_id, "recording", "正在录音，再点一次完成（最多 60 秒）。")
                # The callback caps sample count; this wall-clock limit also
                # closes a stalled driver that never delivers any samples.
                self._stop.wait(timeout=MAX_SECONDS)
                self._stop.set()
            except DictationError:
                raise
            except Exception as exc:
                raise DictationError("无法开启或读取麦克风，请检查设备和 Windows 麦克风权限。") from exc
            finally:
                if stream is not None:
                    try:
                        stream.stop()
                    except Exception:
                        pass
                    try:
                        stream.close()
                    except Exception:
                        pass
                    stream = None
            with self._lock:
                self._recording = False
                if self._cancelled or self._closed:
                    return
                if self._capture_error:
                    raise DictationError("麦克风录音中断，请重新录音。")
                import numpy as np
                samples = np.concatenate(self._chunks) if self._chunks else np.empty(0, dtype=np.float32)
                self._chunks.clear()
            self._emit(job_id, "recognizing", "正在本地识别…")
            samples = _validate_audio(samples, SAMPLE_RATE)
            with self._lock:
                if self._cancelled or self._closed:
                    return
                recognizer = self._recognizer
            if recognizer is None:
                recognizer = _load_recognizer(self.model_dir)
                with self._lock:
                    if self._cancelled or self._closed:
                        return
                    self._recognizer = recognizer
            text = _decode(recognizer, samples, SAMPLE_RATE)
            # Clear busy before publishing terminal events, so immediate retry is safe.
            with self._lock:
                self._busy = False
                self._emit(job_id, "result", "识别完成，录音已清除。", text)
        except DictationError as exc:
            with self._lock:
                self._busy = False
                self._emit(job_id, "error", str(exc))
        except Exception:
            with self._lock:
                self._busy = False
                self._emit(job_id, "error", "语音输入未能完成，请重新录音。")
        finally:
            with self._lock:
                # A newer job may already have started after a terminal event.
                if self._id == job_id:
                    self._recording = False
                    self._busy = False
                    self._chunks.clear()


def self_test(audio_dir, report_path):
    """Explicit diagnostic using fixed official samples, never a microphone.

    This opt-in CLI writes sample transcripts to the requested report only.
    It accepts the official export's PCM16 zh.wav/en.wav samples.
    """
    import json
    import time
    import wave
    import numpy as np

    report = {"engine": "sherpa-onnx", "model": "SenseVoice-Small INT8",
              "audio_source": "official sherpa-onnx export samples", "microphone_used": False,
              "network_used": False, "samples": [], "ok": False}
    try:
        # Load PortAudio and enumerate capability only; never open an input stream.
        import sounddevice as sd
        report["portaudio"] = sd.get_portaudio_version()[1]
        report["input_device_count"] = sum(1 for device in sd.query_devices() if device["max_input_channels"] > 0)
        model_started = time.perf_counter()
        recognizer = _load_recognizer(default_model_path())
        report["model_load_seconds"] = round(time.perf_counter() - model_started, 3)
        for name in ("zh.wav", "en.wav"):
            with wave.open(str(Path(audio_dir) / name), "rb") as audio:
                if audio.getsampwidth() != 2 or audio.getnchannels() != 1:
                    raise DictationError("诊断样例必须为单声道 PCM16 WAV。")
                rate = audio.getframerate()
                if audio.getnframes() > rate * MAX_SECONDS:
                    raise DictationError("诊断样例超过 60 秒。")
                samples = np.frombuffer(audio.readframes(audio.getnframes()), dtype="<i2").astype(np.float32) / 32768.0
            samples = _validate_audio(samples, rate)
            started = time.perf_counter()
            text = _decode(recognizer, samples, rate)
            elapsed = time.perf_counter() - started
            duration = len(samples) / rate
            report["samples"].append({"name": name, "duration_seconds": round(duration, 3),
                                      "recognition_seconds": round(elapsed, 3),
                                      "real_time_factor": round(elapsed / duration, 3), "text": text})
        report["ok"] = True
    except DictationError as exc:
        report["error"] = str(exc)
    except Exception:
        report["error"] = "诊断未能完成，请检查官方音频样例和本地模型。"
    Path(report_path).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if report["ok"] else 1
