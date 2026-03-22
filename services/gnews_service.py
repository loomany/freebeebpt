from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import dataclass
from typing import Any
from urllib import parse, request
from urllib.error import HTTPError, URLError

from services.news_repository import NewsArticleRecord, NewsRepository

logger = logging.getLogger(__name__)

GNEWS_SEARCH_ENDPOINT = "https://gnews.io/api/v4/search"
TOPICS = ("football", "tennis", "hockey", "basketball")
MAX_ARTICLES_PER_REQUEST = 10
DAILY_REQUEST_HARD_LIMIT = 96


class GNewsServiceError(RuntimeError):
    pass


@dataclass(slots=True)
class FetchTopicResult:
    topic: str
    fetched_articles: int
    new_articles: list[dict[str, Any]]
    request_count_today: int
    skipped_due_to_limit: bool = False


class GNewsService:
    def __init__(self, repository: NewsRepository, api_key: str | None = None):
        self.repository = repository
        env_api_key = api_key if api_key is not None else os.getenv("GNEWS_API_KEY")
        self.api_key = env_api_key.strip() if isinstance(env_api_key, str) and env_api_key.strip() else None
        if not self.api_key:
            logger.error("GNEWS_API_KEY не задан")

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    def can_make_request(self) -> bool:
        return self.repository.get_daily_requests() < DAILY_REQUEST_HARD_LIMIT

    def _build_url(self, topic: str, *, page: int = 1, max_results: int = MAX_ARTICLES_PER_REQUEST) -> str:
        query = parse.urlencode(
            {
                "q": topic,
                "lang": "en",
                "max": max_results,
                "sortby": "publishedAt",
                "page": page,
                "apikey": self.api_key or "",
            }
        )
        return f"{GNEWS_SEARCH_ENDPOINT}?{query}"

    async def _request_json(self, url: str) -> dict[str, Any]:
        def _run() -> dict[str, Any]:
            req = request.Request(url)
            try:
                with request.urlopen(req, timeout=30) as response:
                    return json.loads(response.read().decode("utf-8"))
            except HTTPError as error:
                body = error.read().decode("utf-8", errors="replace")
                logger.error("[GNEWS ERROR] status=%s body=%s", error.code, body)
                raise GNewsServiceError(f"HTTP {error.code}: {body}") from error
            except URLError as error:
                logger.error("[GNEWS ERROR] status=network body=%s", error)
                raise GNewsServiceError(str(error)) from error

        return await asyncio.to_thread(_run)

    def _normalize_article(self, topic: str, article: dict[str, Any]) -> NewsArticleRecord:
        title = (article.get("title") or "").strip() or "Untitled"
        description = (article.get("description") or "").strip() or None
        content = (article.get("content") or "").strip() or None
        url = (article.get("url") or "").strip() or None
        published_at = article.get("publishedAt")
        source = article.get("source") or {}
        dedupe_key = self.repository.build_dedupe_key(url, title, published_at)
        return NewsArticleRecord(
            topic=topic,
            url=url,
            title=title,
            description=description,
            content=content,
            published_at=published_at,
            image=(article.get("image") or "").strip() or None,
            source_name=(source.get("name") or "").strip() or None,
            source_url=(source.get("url") or "").strip() or None,
            dedupe_key=dedupe_key,
            raw_payload=json.dumps(article, ensure_ascii=False),
        )

    async def fetch_topic_news(self, topic: str) -> FetchTopicResult:
        logger.info("[NEWS FETCH] topic=%s", topic)
        if not self.api_key:
            logger.error("GNEWS_API_KEY не задан")
            return FetchTopicResult(topic=topic, fetched_articles=0, new_articles=[], request_count_today=self.repository.get_daily_requests())
        if not self.can_make_request():
            logger.warning("[NEWS API LIMIT] daily limit reached, waiting for reset")
            return FetchTopicResult(
                topic=topic,
                fetched_articles=0,
                new_articles=[],
                request_count_today=self.repository.get_daily_requests(),
                skipped_due_to_limit=True,
            )

        url = self._build_url(topic)
        payload = await self._request_json(url)
        today_requests = self.repository.increment_daily_requests()
        logger.info("[NEWS API USAGE] today_requests=%s/100", today_requests)

        articles = payload.get("articles") or []
        logger.info("[NEWS FETCH] topic=%s articles=%s", topic, len(articles))
        new_articles: list[dict[str, Any]] = []
        for article in articles:
            record = self._normalize_article(topic, article)
            if self.repository.save_article(record):
                new_articles.append({
                    "topic": topic,
                    "url": record.url,
                    "title": record.title,
                    "description": record.description,
                    "content": record.content,
                    "published_at": record.published_at,
                    "image": record.image,
                    "source_name": record.source_name,
                    "source_url": record.source_url,
                    "dedupe_key": record.dedupe_key,
                })
        logger.info("[NEWS FETCH] topic=%s new_articles=%s", topic, len(new_articles))
        return FetchTopicResult(topic=topic, fetched_articles=len(articles), new_articles=new_articles, request_count_today=today_requests)

    async def fetch_all_topics(self) -> list[FetchTopicResult]:
        results: list[FetchTopicResult] = []
        for topic in TOPICS:
            results.append(await self.fetch_topic_news(topic))
        self.repository.mark_last_fetch_time()
        return results
