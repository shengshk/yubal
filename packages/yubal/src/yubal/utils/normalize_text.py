"""Normalize music metadata strings for fuzzy matching."""

from __future__ import annotations

import re
import unicodedata

_FEAT_RE = re.compile(
    r"\s*[\(\[]?\s*(?:feat\.?|ft\.?|featuring)\s+.+?[\)\]]?\s*$",
    re.IGNORECASE,
)
_PUNCT_RE = re.compile(r"[\s\-–—_/·•・,，、&＆+＋]+")
_STRIP_RE = re.compile(r'["\'`´\[\]\(\)\{\}【】「」『』《》<>]')
_CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")


def has_cjk(text: str | None) -> bool:
    """True when ``text`` contains CJK ideographs."""
    return bool(text and _CJK_RE.search(text))


# Optional: OpenCC-style 简繁 via zhconv (pure Python)
_zh_convert = None
try:
    from zhconv import convert as _zh_convert  # type: ignore
except ImportError:
    pass

# Optional: strip Latin diacritics (do not apply to CJK)
_unidecode = None
try:
    from unidecode import unidecode as _unidecode
except ImportError:
    pass


def _to_simplified(text: str) -> str:
    if _zh_convert is None:
        return text
    try:
        return _zh_convert(text, "zh-cn")
    except Exception:
        return text


def _latin_fold(text: str) -> str:
    """Unidecode only when the string has no CJK (avoid destroying Chinese)."""
    if _unidecode is None or _CJK_RE.search(text):
        return text
    try:
        return _unidecode(text)
    except Exception:
        return text


def normalize_music_text(value: str | None) -> str:
    """NFKC, 简繁→简, lower, strip feat./punct; Latin diacritics folded when no CJK."""
    if not value:
        return ""
    text = unicodedata.normalize("NFKC", value).strip().lower()
    text = _to_simplified(text)
    text = _FEAT_RE.sub("", text)
    text = _STRIP_RE.sub("", text)
    text = _PUNCT_RE.sub(" ", text)
    text = " ".join(text.split())
    text = _latin_fold(text)
    return " ".join(text.lower().split())


def normalize_artist_key(artists: list[str] | str | None) -> str:
    """Join artists then normalize; order-insensitive via sorted tokens."""
    if artists is None:
        return ""
    if isinstance(artists, str):
        parts = [p.strip() for p in re.split(r"[/&,，、]", artists) if p.strip()]
    else:
        parts = [a.strip() for a in artists if a and a.strip()]
    if not parts:
        return ""
    norms = sorted({normalize_music_text(p) for p in parts if normalize_music_text(p)})
    return " ".join(norms)
