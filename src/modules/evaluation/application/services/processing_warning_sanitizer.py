from __future__ import annotations

import codecs
import unicodedata
from typing import Iterable


_PUNCTUATION_REPLACEMENTS = str.maketrans({
    "\u2013": "-",
    "\u2014": "-",
    "\u2018": "'",
    "\u2019": "'",
    "\u201c": '"',
    "\u201d": '"',
    "\u00a0": " ",
})


def _repair_common_mojibake(text: str) -> str:
    markers = ("\u00c3", "\u00c4", "\u00e2", "\ufffd")
    repaired = text

    for _ in range(2):
        if not any(marker in repaired for marker in markers):
            break
        try:
            candidate = repaired.encode("latin-1").decode("utf-8")
        except UnicodeError:
            break
        if candidate == repaired:
            break
        repaired = candidate

    return repaired


def _decode_escaped_unicode(text: str) -> str:
    if "\\u" not in text and "\\x" not in text:
        return text

    try:
        candidate = codecs.decode(text, "unicode_escape")
    except Exception:
        return text

    return candidate if any(ord(ch) > 127 for ch in candidate) else text


def _clean_warning_text(text: str) -> str:
    normalized = _repair_common_mojibake(text)
    normalized = _decode_escaped_unicode(normalized)
    normalized = unicodedata.normalize("NFC", normalized).translate(
        _PUNCTUATION_REPLACEMENTS
    )
    normalized = (
        normalized
        .replace('\\"', '"')
        .replace("\\'", "'")
        .replace("\\/", "/")
        .replace("\\n", " ")
        .replace("\\r", " ")
        .replace("\\t", " ")
    )
    return " ".join(normalized.split()).strip()


def sanitize_processing_warnings(warnings: Iterable[object]) -> list[str]:
    """
    Normalize warnings to valid UTF-8 text while preserving readable content.
    Repairs common mojibake / escaped-quote issues and removes exact duplicates.
    """
    sanitized: list[str] = []
    seen: set[str] = set()

    for warning in warnings:
        text = _clean_warning_text(str(warning))
        if not text or text in seen:
            continue
        seen.add(text)
        sanitized.append(text)

    return sanitized
