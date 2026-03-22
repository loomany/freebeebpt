from __future__ import annotations

import hashlib
import re
from urllib.parse import urlparse

_WHITESPACE_RE = re.compile(r"\s+")
_PUNCT_RE = re.compile(r"[^\w\s]")


def normalize_title(title: str | None) -> str:
    value = (title or "").strip().lower()
    value = _PUNCT_RE.sub(" ", value)
    return _WHITESPACE_RE.sub(" ", value).strip()


def build_article_hash(url: str | None, source_name: str | None, title: str | None) -> str:
    cleaned_url = (url or "").strip()
    if cleaned_url:
        parsed = urlparse(cleaned_url)
        if parsed.scheme and parsed.netloc:
            return hashlib.sha256(cleaned_url.encode("utf-8")).hexdigest()

    base = f"{(source_name or '').strip().lower()}::{normalize_title(title)}"
    return hashlib.sha256(base.encode("utf-8")).hexdigest()
