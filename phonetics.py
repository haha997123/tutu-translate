"""Offline American English pronunciation lookup.

The bundled CMUdict contains ARPABET, not IPA. Phone symbols and lexical
stress are retained; stress marks are placed using a maximum-onset rule
because CMUdict does not supply syllable boundaries. This is a broad IPA
transcription, not an inference of sentence intonation or word meaning.
"""

from __future__ import annotations

import gzip
import re
import threading
from functools import lru_cache
from pathlib import Path


_PHONES = {
    "AA": "ɑ", "AE": "æ", "AH": "ʌ", "AO": "ɔ", "AW": "aʊ",
    "AY": "aɪ", "B": "b", "CH": "tʃ", "D": "d", "DH": "ð",
    "EH": "ɛ", "ER": "ɝ", "EY": "eɪ", "F": "f", "G": "ɡ",
    "HH": "h", "IH": "ɪ", "IY": "i", "JH": "dʒ", "K": "k",
    "L": "l", "M": "m", "N": "n", "NG": "ŋ", "OW": "oʊ",
    "OY": "ɔɪ", "P": "p", "R": "ɹ", "S": "s", "SH": "ʃ",
    "T": "t", "TH": "θ", "UH": "ʊ", "UW": "u", "V": "v",
    "W": "w", "Y": "j", "Z": "z", "ZH": "ʒ",
}
_VOWELS = frozenset("AA AE AH AO AW AY EH ER EY IH IY OW OY UH UW".split())
_SINGLE_ONSETS = frozenset(_PHONES) - _VOWELS - {"NG"}
# English onsets; rare borrowed names may have a different syllabification.
_CLUSTER_ONSETS = frozenset(
    tuple(cluster.split()) for cluster in (
        "P R", "P L", "P Y", "B R", "B L", "B Y", "T R", "T W", "T Y",
        "D R", "D W", "D Y", "K R", "K L", "K W", "K Y", "G R", "G L",
        "G W", "G Y", "F R", "F L", "F Y", "V R", "V Y", "TH R",
        "TH W", "TH Y", "SH R", "SH W", "S P", "S T", "S K", "S M",
        "S N", "S F", "S W", "S L", "S Y", "HH Y", "M Y", "N Y",
        "L Y", "S P R", "S P L", "S P Y", "S T R", "S T Y", "S K R",
        "S K L", "S K W", "S K Y",
    )
)
_VARIANT = re.compile(r"\(\d+\)$")
_WORD = re.compile(r"'?[a-z]+(?:['-][a-z]+)*'?")
_OUTER_PUNCTUATION = ' \t\r\n.,!?;:()[]{}<>"“”‘’「」『』（）【】，。！？；：、…/\\'
_TYPOGRAPHY = str.maketrans({"’": "'", "‘": "'", "‐": "-", "‑": "-"})


def normalize_word(text: str) -> str | None:
    """Accept one English word, allowing contractions and known compounds.

    Quotes/punctuation around a selected word are harmless. A phrase is never
    silently shortened, and no morphological or spelling guesses are made.
    """
    if not isinstance(text, str) or len(text) > 200:
        return None
    word = text.translate(_TYPOGRAPHY).strip(_OUTER_PUNCTUATION).lower()
    if word.startswith("'") and word.endswith("'") and len(word) > 2:
        word = word[1:-1]
    return word if _WORD.fullmatch(word) else None


def _base(phone: str) -> str:
    return phone.rstrip("012")


def _is_onset(phones: tuple[str, ...]) -> bool:
    return (len(phones) == 1 and phones[0] in _SINGLE_ONSETS) or phones in _CLUSTER_ONSETS


@lru_cache(maxsize=4096)
def arpabet_to_ipa(pronunciation: str) -> str:
    """Convert dictionary phones; reject unknown symbols rather than invent.

    American broad IPA convention: /ɹ/ for R, /ɝ/ for stressed ER, /ɚ/ for
    unstressed ER; phonemic length marks are omitted. A stressed monosyllable
    does not need a stress mark. Multiple lexical stresses remain visible.
    """
    tokens = pronunciation.split()
    if not tokens or any(_base(token) not in _PHONES for token in tokens):
        raise ValueError("Unsupported pronunciation symbols")
    nuclei = [i for i, token in enumerate(tokens) if _base(token) in _VOWELS]
    stress_positions: dict[int, str] = {}
    if len(nuclei) > 1:
        for index, nucleus in enumerate(nuclei):
            stress = tokens[nucleus][-1]
            if stress not in "12":
                continue
            if index == 0:
                start = 0
            else:
                previous = nuclei[index - 1]
                cluster = tuple(_base(phone) for phone in tokens[previous + 1:nucleus])
                onset_length = 0
                for length in range(min(3, len(cluster)), 0, -1):
                    if _is_onset(cluster[-length:]):
                        onset_length = length
                        break
                start = nucleus - onset_length
            stress_positions[start] = "ˈ" if stress == "1" else "ˌ"

    output = []
    for i, token in enumerate(tokens):
        output.append(stress_positions.get(i, ""))
        if token == "AH0":
            output.append("ə")
        elif token == "ER0":
            output.append("ɚ")
        else:
            output.append(_PHONES[_base(token)])
    return "".join(output)


class Phonetics:
    """Lazy, thread-safe lookup in a bundled dictionary. No runtime network."""

    def __init__(self, assets_path: Path | None = None):
        self.assets_path = Path(assets_path) if assets_path is not None else Path(__file__).parent / "assets"
        self._entries: dict[str, list[str]] | None = None
        self._load_lock = threading.Lock()

    def _load(self) -> dict[str, list[str]]:
        if self._entries is None:
            with self._load_lock:
                if self._entries is None:
                    entries: dict[str, list[str]] = {}
                    with gzip.open(self.assets_path / "cmudict.dict.gz", "rt", encoding="utf-8") as source:
                        for line in source:
                            line = line.split("#", 1)[0].strip()
                            if not line or line.startswith(";;;"):
                                continue
                            spelling, pronunciation = line.split(maxsplit=1)
                            word = _VARIANT.sub("", spelling).lower()
                            variants = entries.setdefault(word, [])
                            if pronunciation not in variants:
                                variants.append(pronunciation)
                    self._entries = entries
        return self._entries

    def lookup(self, text: str) -> dict | None:
        """Return all distinct dictionary pronunciations for one word.

        None means a phrase, an unsupported spelling, or an absent entry.
        Missing/corrupt assets raise their normal exceptions so callers can
        distinguish an installation problem from an unknown word.
        """
        word = normalize_word(text)
        if word is None:
            return None
        variants = self._load().get(word)
        if variants is None:
            return None
        ipa = list(dict.fromkeys(arpabet_to_ipa(variant) for variant in variants))
        return {
            "word": word,
            "ipa": ipa,
            "accent": "美式",
            "approximate": True,
            "source": "CMUdict",
            "note": "美式词典转写；重音符号按音节规则整理，多音词未按上下文筛选。",
        }
