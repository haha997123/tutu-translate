"""Small CPU-only Argos model runner, with no network or model downloads.

Uses the published Argos 1.9 CTranslate2 weights and SentencePiece tokenizer
directly. Neither torch nor Stanza is required. Models are shipped beside the
application in ``models/``; see that directory's provenance and licenses.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import sys
import threading


MAX_TEXT_LENGTH = 5000
MAX_CHUNK_CHARS = 240
MAX_INPUT_TOKENS = 240


class OfflineTranslationError(Exception):
    """A concise error safe to display without user text or local paths."""


def default_models_path() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent / "models"
    return Path(__file__).resolve().parent / "models"


def _chunks(text: str, limit: int = MAX_CHUNK_CHARS) -> list[str]:
    """Bound each sentence without dropping characters, whitespace or Unicode."""
    if limit < 2:
        raise ValueError("Chunk limit must be at least two")
    chunks = []
    start = 0
    for index, char in enumerate(text):
        end = index + 1
        sentence_end = char in "。！？!?;；" or (
            char == "." and (end == len(text) or text[end].isspace())
        )
        if sentence_end or end - start >= limit:
            if not sentence_end and end < len(text):
                boundary = text.rfind(" ", start + limit // 2, end)
                if boundary >= 0:
                    end = boundary + 1
            if end > start:
                chunks.append(text[start:end])
                start = end
    if start < len(text):
        chunks.append(text[start:])
    return chunks


@dataclass
class _Model:
    translator: object
    tokenizer: object
    target_prefix: str


class OfflineTranslator:
    """Lazy, thread-safe local runner with bounded CPU concurrency.

    ``warmup`` loads and runs both directions once. Call it on a background
    thread during startup, so a later network failure has no model-loading delay.
    """

    def __init__(self, models_path: str | Path | None = None):
        self.models_path = Path(models_path) if models_path is not None else default_models_path()
        self._models: dict[tuple[str, str], _Model] = {}
        self._load_lock = threading.RLock()
        self._inference_lock = threading.Lock()

    @property
    def ready(self) -> bool:
        with self._load_lock:
            return len(self._models) == 2

    def _load(self, source: str, target: str) -> _Model:
        key = (source, target)
        if key not in {("zh", "en"), ("en", "zh")}:
            raise OfflineTranslationError("离线翻译仅支持中文和英文。")
        with self._load_lock:
            if key in self._models:
                return self._models[key]
            root = self.models_path / f"{source}_{target}"
            try:
                metadata = json.loads((root / "metadata.json").read_text(encoding="utf-8"))
                if metadata.get("from_code") != source or metadata.get("to_code") != target:
                    raise ValueError("Wrong model direction")
                if not (root / "model" / "model.bin").is_file():
                    raise FileNotFoundError("Missing weights")
                if not (root / "sentencepiece.model").is_file():
                    raise FileNotFoundError("Missing tokenizer")
                import ctranslate2
                import sentencepiece
                translator = ctranslate2.Translator(
                    str(root / "model"), device="cpu", compute_type="int8",
                    inter_threads=1, intra_threads=min(4, os.cpu_count() or 2),
                    max_queued_batches=2,
                )
                # SentencePiece's native Windows reader cannot open some
                # non-ASCII paths. Python reads them correctly; passing the
                # small serialized tokenizer bypasses that native reader.
                tokenizer = sentencepiece.SentencePieceProcessor(
                    model_proto=(root / "sentencepiece.model").read_bytes()
                )
                model = _Model(translator, tokenizer, metadata.get("target_prefix", ""))
            except Exception:
                raise OfflineTranslationError(
                    "本地离线模型缺失或无法读取，请完整解压软件后再试。"
                ) from None
            self._models[key] = model
            return model

    def warmup(self) -> bool:
        self.translate("Hello.", "en", "zh")
        self.translate("你好。", "zh", "en")
        return self.ready

    def translate(self, text: str, source: str, target: str) -> str:
        if not isinstance(text, str) or not text.strip():
            raise OfflineTranslationError("先输入需要翻译的文字吧。")
        if len(text) > MAX_TEXT_LENGTH:
            raise OfflineTranslationError("每次最多翻译 5000 个字符，请分段粘贴。")
        model = self._load(source, target)
        # Keep paragraph separators and outside whitespace exact. SentencePiece
        # normalizes its own input whitespace, so do not give it separators.
        pieces: list[tuple[str, str, str] | str] = []
        tokenized: list[list[str]] = []
        for paragraph in re.split(r"(\n+)", text.replace("\r\n", "\n").replace("\r", "\n")):
            if not paragraph or paragraph.startswith("\n") or not paragraph.strip():
                pieces.append(paragraph)
                continue
            for chunk in _chunks(paragraph):
                if not chunk.strip():
                    pieces.append(chunk)
                    continue
                prefix = chunk[:len(chunk) - len(chunk.lstrip())]
                suffix = chunk[len(chunk.rstrip()):]
                tokens = model.tokenizer.encode(chunk.strip(), out_type=str)
                # Inputs consisting of many distinct glyphs can tokenize longer
                # than their character count. Split tokens rather than truncate.
                for start in range(0, len(tokens), MAX_INPUT_TOKENS):
                    last = start + MAX_INPUT_TOKENS >= len(tokens)
                    pieces.append((prefix if start == 0 else "", suffix if last else "", "translate"))
                    tokenized.append(tokens[start:start + MAX_INPUT_TOKENS])
        if not tokenized:
            return text
        try:
            with self._inference_lock:
                batches = model.translator.translate_batch(
                    tokenized, target_prefix=[[model.target_prefix]] * len(tokenized) if model.target_prefix else None,
                    beam_size=2, num_hypotheses=1, length_penalty=0.2,
                    replace_unknowns=True, max_batch_size=8, batch_type="examples",
                    max_input_length=MAX_INPUT_TOKENS, max_decoding_length=512,
                    repetition_penalty=1.05,
                )
            translated = []
            for batch in batches:
                value = model.tokenizer.decode(batch.hypotheses[0]).replace("▁", " ")
                if model.target_prefix and value.startswith(model.target_prefix):
                    value = value[len(model.target_prefix):]
                value = value.strip()
                if target == "zh":
                    value = re.sub(r"[ \t]+([，。！？；：、])", r"\1", value)
                if not value:
                    raise ValueError("Empty local result")
                translated.append(value)
            if len(translated) != len(tokenized):
                raise ValueError("Incomplete local result")
            results = iter(translated)
            output = []
            for piece in pieces:
                if isinstance(piece, str):
                    output.append(piece)
                    continue
                prefix, suffix, _ = piece
                value = next(results)
                if target == "en" and output and not prefix and output[-1] and not output[-1][-1].isspace():
                    prefix = " "
                output.append(prefix + value + suffix)
            value = "".join(output)
            # Some compact OPUS word lookups echo the same complete translation
            # twice. Restrict cleanup to a single English lexeme and identical
            # whitespace-delimited blocks; sentences and Chinese reduplication
            # such as 妈妈/哈哈 remain unchanged.
            if source == "en" and target == "zh" and re.fullmatch(r"[A-Za-z]+(?:['’\-][A-Za-z]+)*", text.strip()):
                blocks = value.split()
                if len(blocks) > 1 and all(block == blocks[0] for block in blocks):
                    prefix = value[:len(value) - len(value.lstrip())]
                    suffix = value[len(value.rstrip()):]
                    value = prefix + blocks[0] + suffix
            return value
        except OfflineTranslationError:
            raise
        except Exception:
            raise OfflineTranslationError("本地翻译暂时没有成功，请换一段文字试试。") from None


def self_test(models_path: str | Path | None = None, report_path: str | Path | None = None) -> dict:
    """Run real CPU inference with all HTTP disabled, then forced-failure fallback.

    Suitable for a development interpreter or the packaged app's diagnostic
    switch. Test phrases are synthetic and contain no user clipboard content.
    Raises on failure; a saved report contains actual output and elapsed times.
    """
    import importlib.metadata
    import time
    from unittest.mock import patch
    import requests
    from engine import Translator

    local = OfflineTranslator(models_path)
    engine = Translator(offline_translator=local, cache_size=0)
    started = time.monotonic()
    with patch("requests.sessions.Session.request", side_effect=AssertionError("Unexpected network in warmup")):
        warmup = engine.warmup()
    if not warmup["available"]:
        raise OfflineTranslationError(warmup["error"])
    report = {"warmup_seconds": round(time.monotonic() - started, 4), "tests": [],
              "versions": {name: importlib.metadata.version(name) for name in ("ctranslate2", "sentencepiece")},
              "model_bytes": sum(path.stat().st_size for path in local.models_path.rglob("*") if path.is_file())}
    phrases = ["A little rabbit likes carrots.", "我今天想去公园散步。",
               "Please save this document.\n\nThe meeting starts at nine.", "你好。\n\n谢谢你的帮助。"]
    with patch("requests.sessions.Session.request", side_effect=AssertionError("Unexpected network in local translation")):
        for text in phrases:
            result = engine.translate(text, offline=True)
            if result["provider"] != "本地离线" or not result["text"].strip():
                raise RuntimeError("Offline translation did not produce text")
            if "\n\n" in text and "\n\n" not in result["text"]:
                raise RuntimeError("Offline paragraph structure lost")
            report["tests"].append({"case": "true_offline", "input": text, **result})
    engine.clear_cache()
    with patch("requests.sessions.Session.request", side_effect=requests.Timeout("synthetic offline test")) as request:
        for text in phrases[:2]:
            result = engine.translate(text)
            if result["provider"] != "本地离线":
                raise RuntimeError("Network failure did not select offline model")
            report["tests"].append({"case": "forced_network_timeout", "input": text, **result})
        report["failed_online_requests"] = request.call_count
    if report["failed_online_requests"] != 1:
        raise RuntimeError("Network cooldown did not prevent repeated failed requests")
    # A connection that stays silent must also stop delaying the local result.
    import types
    def delayed_response(*args, **kwargs):
        time.sleep(1.3)
        return types.SimpleNamespace(status_code=200, encoding="utf-8", raise_for_status=lambda: None,
                                     json=lambda: [[["Late network response"]]])
    with engine._lock:
        engine._cooldown_until = 0.0
    with patch("requests.sessions.Session.request", side_effect=delayed_response):
        result = engine.translate("The little rabbit is reading a book.")
    if result["provider"] != "本地离线" or result["elapsed"] > 1.2:
        raise RuntimeError("Slow network was allowed to delay the local model")
    report["tests"].append({"case": "slow_online_deadline", "input": "The little rabbit is reading a book.", **result})
    engine.close()
    report["passed"] = True
    if report_path is not None:
        Path(report_path).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report
