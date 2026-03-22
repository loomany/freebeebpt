from __future__ import annotations

import asyncio
import html
import logging
import re
from typing import Any
from urllib import request
from urllib.error import URLError

try:
    import trafilatura
except ImportError:  # pragma: no cover
    trafilatura = None

try:
    from bs4 import BeautifulSoup
except ImportError:  # pragma: no cover
    BeautifulSoup = None

try:
    from readability import Document
except ImportError:  # pragma: no cover
    Document = None

logger = logging.getLogger(__name__)

_NOISE_PATTERNS = [
    r"subscribe now",
    r"sign up for newsletters?",
    r"advertisement",
    r"read more",
    r"click here",
    r"cookie policy",
    r"all rights reserved",
]


def clean_article_text(text: str) -> str:
    value = html.unescape(text or "")
    value = re.sub(r"\[\+\d+ chars\]", "", value, flags=re.IGNORECASE)
    lines: list[str] = []
    seen: set[str] = set()
    for raw_line in value.splitlines():
        line = re.sub(r"\s+", " ", raw_line).strip()
        if not line or len(line) < 2:
            continue
        lowered = line.lower()
        if any(re.search(pattern, lowered, flags=re.IGNORECASE) for pattern in _NOISE_PATTERNS):
            continue
        dedupe_key = lowered[:240]
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        lines.append(line)
    cleaned = "\n\n".join(lines)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


async def _download_html(url: str, timeout: int) -> str:
    def _run() -> str:
        req = request.Request(url, headers={"User-Agent": "Mozilla/5.0 NewsBot/1.0"})
        with request.urlopen(req, timeout=timeout) as response:
            charset = response.headers.get_content_charset() or "utf-8"
            return response.read().decode(charset, errors="replace")

    return await asyncio.to_thread(_run)


async def extract_article_text(url: str, timeout: int = 15, min_text_length: int = 800) -> dict[str, Any]:
    result: dict[str, Any] = {
        "success": False,
        "title": None,
        "text": None,
        "top_image": None,
        "authors": [],
        "publish_date": None,
        "method": "none",
        "error": None,
    }
    try:
        if trafilatura is not None:
            downloaded = await asyncio.wait_for(asyncio.to_thread(trafilatura.fetch_url, url), timeout=timeout)
            if downloaded:
                extracted = await asyncio.to_thread(
                    trafilatura.extract,
                    downloaded,
                    include_comments=False,
                    include_images=True,
                    include_links=False,
                    favor_precision=True,
                )
                cleaned = clean_article_text(extracted or "")
                if len(cleaned) >= min_text_length:
                    metadata = await asyncio.to_thread(trafilatura.extract_metadata, downloaded)
                    result.update(
                        success=True,
                        title=getattr(metadata, "title", None),
                        text=cleaned,
                        top_image=getattr(metadata, "image", None),
                        authors=getattr(metadata, "author", []) if metadata else [],
                        publish_date=str(getattr(metadata, "date", None)) if metadata else None,
                        method="trafilatura",
                    )
                    return result
    except Exception as error:  # noqa: BLE001
        logger.warning("[ARTICLE EXTRACT] trafilatura failed url=%s error=%s", url, error)
        result["error"] = str(error)

    html_doc = ""
    try:
        html_doc = await _download_html(url, timeout)
    except (TimeoutError, URLError, Exception) as error:  # noqa: BLE001
        logger.warning("[ARTICLE EXTRACT] download failed url=%s error=%s", url, error)
        result["error"] = str(error)
        return result

    try:
        if Document is None:
            raise RuntimeError("readability is not installed")
        readable_html = await asyncio.to_thread(lambda: Document(html_doc).summary())
        if BeautifulSoup is None:
            raise RuntimeError("beautifulsoup4 is not installed")
        soup = BeautifulSoup(readable_html, "html.parser")
        text = clean_article_text("\n".join(p.get_text(" ", strip=True) for p in soup.select("p")))
        if len(text) >= min_text_length:
            image = None
            og_image = BeautifulSoup(html_doc, "html.parser").find("meta", attrs={"property": "og:image"}) if BeautifulSoup is not None else None
            if og_image:
                image = og_image.get("content")
            result.update(success=True, text=text, top_image=image, method="readability")
            return result
    except Exception as error:  # noqa: BLE001
        logger.warning("[ARTICLE EXTRACT] readability failed url=%s error=%s", url, error)
        result["error"] = str(error)

    try:
        if BeautifulSoup is None:
            raise RuntimeError("beautifulsoup4 is not installed")
        soup = BeautifulSoup(html_doc, "html.parser")
        container = soup.find("article") or soup.body or soup
        text = clean_article_text("\n".join(node.get_text(" ", strip=True) for node in container.select("p")))
        if len(text) >= min_text_length:
            image = None
            og_image = soup.find("meta", attrs={"property": "og:image"})
            if og_image:
                image = og_image.get("content")
            result.update(success=True, title=soup.title.string.strip() if soup.title and soup.title.string else None, text=text, top_image=image, method="bs4")
            return result
        result["text"] = text or None
        result["method"] = "bs4"
        result["error"] = result.get("error") or "full article not found"
        return result
    except Exception as error:  # noqa: BLE001
        logger.warning("[ARTICLE EXTRACT] html parsing failed url=%s error=%s", url, error)
        result["error"] = str(error)
        return result


def build_fallback_text(article: dict[str, Any], extracted_text: str | None, min_text_length: int = 800) -> str:
    if extracted_text and len(clean_article_text(extracted_text)) >= min_text_length:
        return clean_article_text(extracted_text)

    content = clean_article_text(article.get("content") or "")
    if content:
        return content

    parts = [article.get("title") or "", article.get("description") or ""]
    return clean_article_text("\n\n".join(part for part in parts if part))
