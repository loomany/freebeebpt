from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import dataclass
from typing import Any
from urllib import parse, request
from urllib.error import HTTPError, URLError

logger = logging.getLogger(__name__)


class GNewsFetchError(RuntimeError):
    pass


@dataclass(slots=True)
class FetchResult:
    topic: str
    fetched_articles: int
    articles: list[dict[str, Any]]
    request_count_today: int
    skipped_due_to_limit: bool = False


class NewsFetcher:
    def __init__(self, repository, api_key: str | None = None):
        self.repository = repository
        self.api_key = (api_key if api_key is not None else os.getenv("GNEWS_API_KEY", "")).strip() or None
        self.base_url = os.getenv("GNEWS_BASE_URL", "https://gnews.io/api/v4").rstrip("/")
        self.language = os.getenv("GNEWS_LANGUAGE", "en")
        self.max_results = max(1, int(os.getenv("GNEWS_MAX_RESULTS", "5")))
        self.daily_request_limit = max(1, int(os.getenv("GNEWS_DAILY_LIMIT", "96")))

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    def can_make_request(self) -> bool:
        return self.repository.get_daily_requests() < self.daily_request_limit

    def build_url(self, topic: str, *, lang: str | None = None, max_results: int | None = None) -> str:
        query = parse.urlencode(
            {
                "q": topic,
                "lang": lang or self.language,
                "max": max_results or self.max_results,
                "sortby": "publishedAt",
                "apikey": self.api_key or "",
            }
        )
        return f"{self.base_url}/search?{query}"

    async def _request_json(self, url: str) -> dict[str, Any]:
        def _run() -> dict[str, Any]:
            req = request.Request(url)
            try:
                with request.urlopen(req, timeout=30) as response:
                    return json.loads(response.read().decode("utf-8"))
            except HTTPError as error:
                body = error.read().decode("utf-8", errors="replace")
                logger.error("[GNEWS ERROR] status=%s body=%s", error.code, body)
                raise GNewsFetchError(f"HTTP {error.code}: {body}") from error
            except URLError as error:
                logger.error("[GNEWS ERROR] network=%s", error)
                raise GNewsFetchError(str(error)) from error

        return await asyncio.to_thread(_run)

    async def fetch_gnews_articles(self, topic: str, lang: str = "en", max_results: int | None = None) -> FetchResult:
        logger.info("[GNEWS REQUEST] topic=%s", topic)
        if not self.configured:
            logger.error("[GNEWS REQUEST] api key is not configured")
            return FetchResult(topic=topic, fetched_articles=0, articles=[], request_count_today=self.repository.get_daily_requests())
        if not self.can_make_request():
            logger.warning("[GNEWS REQUEST] skipped because daily limit reached")
            return FetchResult(
                topic=topic,
                fetched_articles=0,
                articles=[],
                request_count_today=self.repository.get_daily_requests(),
                skipped_due_to_limit=True,
            )

        payload = await self._request_json(self.build_url(topic, lang=lang, max_results=max_results))
        request_count = self.repository.increment_daily_requests()
        articles = payload.get("articles") or []
        logger.info("[GNEWS RESPONSE] topic=%s fetched=%s requests_today=%s", topic, len(articles), request_count)
        return FetchResult(topic=topic, fetched_articles=len(articles), articles=articles, request_count_today=request_count)
