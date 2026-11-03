"""Online-first Chinese/English translation with fast local fallback.

Google's public web endpoint is an unsupported convenience interface. After a
short grace period the bundled CPU model runs; no second online-service chain.
Only submitted text is sent remotely; cache and results remain in memory.
"""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
import queue
import re
import threading
import time

import requests

from offline import OfflineTranslationError, OfflineTranslator

MAX_TEXT_LENGTH = 5000
GOOGLE_URL = "https://translate.googleapis.com/translate_a/single"
_HAN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\U00020000-\U000323af]")


class TranslationError(Exception):
    def __init__(self, message: str, code: str = "service"):
        super().__init__(message)
        self.code = code


@dataclass
class _Pending:
    ready: threading.Event = field(default_factory=threading.Event)
    result: dict | None = None
    error: TranslationError | None = None


@dataclass
class _OnlineResult:
    ready: threading.Event = field(default_factory=threading.Event)
    text: str | None = None
    error: TranslationError | None = None


def _direction(text: str, mode: str = "auto") -> tuple[str, str]:
    if mode == "zh-en":
        return "zh", "en"
    if mode == "en-zh":
        return "en", "zh"
    if mode != "auto":
        raise TranslationError("仅支持中文和英文翻译。", "input")
    return ("zh", "en") if _HAN.search(text) else ("en", "zh")


class Translator:
    """Thread-safe, bounded online work and a memory-only result cache.

    At most two daemon network workers exist. Late requests cannot update a
    returned result or cache entry; OS DNS stalls cannot create an unbounded
    background queue or keep the application alive.
    """

    def __init__(self, cache_size: int = 256, cache_ttl: float = 1800.0,
                 connect_timeout: float = 0.5, read_timeout: float = 1.2,
                 request_budget: float = 1.7, online_grace: float = 0.8,
                 models_path: str | Path | None = None,
                 offline_translator: OfflineTranslator | None = None):
        if cache_size < 0 or min(cache_ttl, connect_timeout, read_timeout, request_budget, online_grace) <= 0:
            raise ValueError("Cache size must be nonnegative and timeouts positive")
        self._cache_size, self._cache_ttl = cache_size, cache_ttl
        self._connect_timeout, self._read_timeout = connect_timeout, read_timeout
        self._request_budget = request_budget
        self._online_grace = min(online_grace, request_budget)
        self._offline = offline_translator if offline_translator is not None else OfflineTranslator(models_path)
        self._cache: OrderedDict[tuple, tuple[float, dict]] = OrderedDict()
        self._pending: dict[tuple, _Pending] = {}
        self._cooldown_until = 0.0
        self._lock = threading.RLock()
        self._network_slots = threading.BoundedSemaphore(2)
        self._network_jobs: queue.Queue = queue.Queue(maxsize=2)
        self._network_started = False
        self._local = threading.local()
        self._closed = False

    def warmup(self) -> dict:
        """Background-safe startup probe; never makes a network request."""
        try:
            available = bool(self._offline.warmup())
            return {"available": available, "ready": available, "error": None}
        except OfflineTranslationError as error:
            return {"available": False, "ready": False, "error": str(error)}
        except Exception:
            return {"available": False, "ready": False, "error": "本地离线翻译未准备好。"}

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
        for _ in range(2):
            try:
                self._network_jobs.put_nowait(None)
            except queue.Full:
                break

    def clear_cache(self) -> None:
        with self._lock:
            self._cache.clear()

    def translate_offline(self, text: str, mode: str = "auto") -> dict:
        return self.translate(text, mode, offline=True)

    def translate(self, text: str, mode: str = "auto", offline: bool = False) -> dict:
        started = time.monotonic()
        if not isinstance(text, str):
            raise TranslationError("先输入需要翻译的文字吧。", "input")
        text = text.replace("\r\n", "\n").replace("\r", "\n").strip()
        if not text:
            raise TranslationError("先输入需要翻译的文字吧。", "input")
        if len(text) > MAX_TEXT_LENGTH:
            raise TranslationError("每次最多翻译 5000 个字符，请分段粘贴。", "input")
        try:
            text.encode("utf-8")
        except UnicodeEncodeError:
            raise TranslationError("输入包含无法处理的字符，请换一段文字试试。", "input") from None
        source, target = _direction(text, mode)
        key = (text, source, target, bool(offline))
        with self._lock:
            entry = self._cache.get(key)
            if entry and started - entry[0] < self._cache_ttl:
                self._cache.move_to_end(key)
                return {**entry[1], "elapsed": time.monotonic() - started, "cached": True}
            self._cache.pop(key, None)
            pending = self._pending.get(key)
            owner = pending is None
            if owner:
                pending = _Pending()
                self._pending[key] = pending
        assert pending is not None
        if not owner:
            if not pending.ready.wait(120.0):
                raise TranslationError("这段文字较长，请分成较短段落重试。", "timeout")
            if pending.error:
                raise TranslationError(str(pending.error), pending.error.code)
            assert pending.result is not None
            return {**pending.result, "elapsed": time.monotonic() - started, "cached": True}
        try:
            translated, provider = self._translate(text, source, target, offline)
            result = {"text": translated, "source": source, "target": target, "provider": provider,
                      "elapsed": time.monotonic() - started, "cached": False}
            with self._lock:
                if self._cache_size:
                    self._cache[key] = (time.monotonic(), dict(result))
                    self._cache.move_to_end(key)
                    while len(self._cache) > self._cache_size:
                        self._cache.popitem(last=False)
                pending.result = dict(result)
            return result
        except TranslationError as error:
            pending.error = error
            raise
        except Exception:
            error = TranslationError("翻译暂时没有成功，请换一段文字试试。")
            pending.error = error
            raise error from None
        finally:
            with self._lock:
                self._pending.pop(key, None)
                pending.ready.set()

    def _start_online(self, text: str, source: str, target: str) -> _OnlineResult | None:
        with self._lock:
            if self._closed or self._cooldown_until > time.monotonic():
                return None
            if not self._network_started:
                self._network_started = True
                for index in range(2):
                    threading.Thread(target=self._network_loop, name=f"TutuOnline{index + 1}", daemon=True).start()
        if not self._network_slots.acquire(blocking=False):
            return None
        result = _OnlineResult()
        self._network_jobs.put_nowait((text, source, target, result))
        return result

    def _network_loop(self):
        # Each persistent worker reuses one HTTPS session for successive texts.
        while True:
            job = self._network_jobs.get()
            if job is None:
                session = getattr(self._local, "session", None)
                if session is not None:
                    session.close()
                return
            text, source, target, result = job
            try:
                value = self._google(text, source, target, time.monotonic() + self._request_budget)
                if not isinstance(value, str) or not value.strip():
                    raise TranslationError("翻译服务没有返回译文。")
                result.text = value
                with self._lock:
                    self._cooldown_until = 0.0
            except Exception as error:
                result.error = error if isinstance(error, TranslationError) else TranslationError("联网翻译暂时不可用。")
                with self._lock:
                    self._cooldown_until = time.monotonic() + (180.0 if result.error.code == "quota" else 30.0)
            finally:
                result.ready.set()
                self._network_slots.release()

    def _translate(self, text: str, source: str, target: str, offline: bool) -> tuple[str, str]:
        online = None if offline else self._start_online(text, source, target)
        if online is not None:
            if online.ready.wait(self._online_grace) and online.text is not None:
                return online.text, "Google"
            if not online.ready.is_set():
                with self._lock:
                    self._cooldown_until = max(self._cooldown_until, time.monotonic() + 5.0)
        try:
            return self._offline.translate(text, source, target), "本地离线"
        except OfflineTranslationError as error:
            if online is not None and online.ready.wait(self._request_budget) and online.text is not None:
                return online.text, "Google"
            raise TranslationError(str(error), "offline") from None

    def _session(self) -> requests.Session:
        session = getattr(self._local, "session", None)
        if session is None:
            session = requests.Session()
            session.headers.update({"User-Agent": "TutuTranslate/2.0", "Accept": "application/json"})
            self._local.session = session
        return session

    @staticmethod
    def _timeout_error() -> TranslationError:
        return TranslationError("联网翻译响应较慢。", "timeout")

    def _request(self, method: str, url: str, deadline: float, **kwargs) -> object:
        remaining = deadline - time.monotonic()
        if remaining <= 0.05:
            raise self._timeout_error()
        timeout = (min(self._connect_timeout, remaining / 2), min(self._read_timeout, remaining / 2))
        try:
            response = self._session().request(method, url, timeout=timeout, allow_redirects=False, **kwargs)
            if response.status_code == 429:
                raise TranslationError("联网翻译暂时限流。", "quota")
            if response.status_code in (401, 403):
                raise TranslationError("联网翻译暂时无法访问。", "access")
            response.raise_for_status()
            if response.status_code != 200:
                raise TranslationError("联网翻译返回异常。")
            response.encoding = "utf-8"
            return response.json()
        except TranslationError:
            raise
        except requests.Timeout:
            raise self._timeout_error() from None
        except requests.ConnectionError:
            raise TranslationError("暂时连接不上翻译服务。", "network") from None
        except requests.RequestException:
            raise TranslationError("联网翻译暂时不可用。") from None
        except (ValueError, TypeError):
            raise TranslationError("联网翻译返回异常。") from None

    def _google(self, text: str, source: str, target: str, deadline: float) -> str:
        codes = {"zh": "zh-CN", "en": "en"}
        data = self._request("POST", GOOGLE_URL, deadline,
                             data={"client": "gtx", "sl": codes[source], "tl": codes[target], "dt": "t", "q": text})
        try:
            segments = data[0]
            if not isinstance(segments, list) or not segments:
                raise ValueError
            translated = []
            for segment in segments:
                if not isinstance(segment, list) or not segment or not isinstance(segment[0], str):
                    raise ValueError
                translated.append(segment[0])
            return "".join(translated)
        except (IndexError, KeyError, TypeError, ValueError):
            raise TranslationError("联网翻译返回异常。") from None
