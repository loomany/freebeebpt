from __future__ import annotations

import hashlib
import re
from urllib.parse import urlparse

_WHITESPACE_RE = re.compile(r"\s+")
_PUNCT_RE = re.compile(r"[^\w\s]")
_TOKEN_RE = re.compile(r"\w+", re.UNICODE)
_STOP_WORDS = {
    "a", "an", "and", "as", "at", "by", "for", "from", "in", "into", "of", "on", "or", "the", "to", "vs", "with",
    "after", "before", "over", "under", "through", "out", "off", "up", "down",
}
_COMMON_SUFFIXES = ("ing", "ed", "es", "s")


def normalize_title(title: str | None) -> str:
    value = (title or "").strip().lower()
    value = _PUNCT_RE.sub(" ", value)
    return _WHITESPACE_RE.sub(" ", value).strip()


def _normalize_token(token: str) -> str:
    value = token.strip().lower()
    if len(value) <= 2 or value.isdigit() or value in _STOP_WORDS:
        return ""
    for suffix in _COMMON_SUFFIXES:
        if value.endswith(suffix) and len(value) - len(suffix) >= 4:
            value = value[: -len(suffix)]
            break
    return value


def title_fingerprint(title: str | None) -> frozenset[str]:
    normalized = normalize_title(title)
    tokens = {_normalize_token(token) for token in _TOKEN_RE.findall(normalized)}
    return frozenset(token for token in tokens if token)


def titles_look_duplicate(first_title: str | None, second_title: str | None) -> bool:
    first = title_fingerprint(first_title)
    second = title_fingerprint(second_title)
    if not first or not second:
        return False
    overlap = len(first & second)
    shortest = min(len(first), len(second))
    return overlap >= 4 and (overlap / shortest) >= 0.5


def build_article_hash(url: str | None, source_name: str | None, title: str | None) -> str:
    cleaned_url = (url or "").strip()
    if cleaned_url:
        parsed = urlparse(cleaned_url)
        if parsed.scheme and parsed.netloc:
            return hashlib.sha256(cleaned_url.encode("utf-8")).hexdigest()

    base = f"{(source_name or '').strip().lower()}::{normalize_title(title)}"
    return hashlib.sha256(base.encode("utf-8")).hexdigest()
