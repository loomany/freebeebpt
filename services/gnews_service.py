from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from typing import Any

from services.dedup import build_article_hash
from services.news_fetcher import FetchResult, NewsFetcher
from services.news_repository import NewsArticleRecord, NewsRepository

logger = logging.getLogger(__name__)

TOPICS = tuple(topic.strip() for topic in os.getenv("NEWS_TOPICS", "football,tennis,hockey,basketball").split(",") if topic.strip())


@dataclass(slots=True)
class FetchTopicResult:
    topic: str
    fetched_articles: int
    new_articles: list[dict[str, Any]]
    request_count_today: int
    skipped_due_to_limit: bool = False


class GNewsService:
    TOPICS = TOPICS

    def __init__(self, repository: NewsRepository, api_key: str | None = None):
        self.repository = repository
        self.fetcher = NewsFetcher(repository=repository, api_key=api_key)

    @property
    def configured(self) -> bool:
        return self.fetcher.configured

    async def fetch_gnews_articles(self, topic: str, lang: str = "en", max_results: int | None = None) -> list[dict[str, Any]]:
        result = await self.fetcher.fetch_gnews_articles(topic=topic, lang=lang, max_results=max_results)
        return result.articles

    def _normalize_article(self, topic: str, article: dict[str, Any]) -> dict[str, Any]:
        source = article.get("source") or {}
        title = (article.get("title") or "").strip() or "Untitled"
        return {
            "topic": topic,
            "title": title,
            "description": (article.get("description") or "").strip() or None,
            "content": (article.get("content") or "").strip() or None,
            "source_name": (source.get("name") or "").strip() or None,
            "source_url": (source.get("url") or "").strip() or None,
            "url": (article.get("url") or "").strip() or None,
            "published_at": article.get("publishedAt"),
        }

    async def fetch_topic_news(self, topic: str) -> FetchTopicResult:
        fetch_result: FetchResult = await self.fetcher.fetch_gnews_articles(topic)
        new_articles: list[dict[str, Any]] = []
        for raw_article in fetch_result.articles:
            article = self._normalize_article(topic, raw_article)
            article_hash = build_article_hash(article.get("url"), article.get("source_name"), article.get("title"))
            article["article_hash"] = article_hash
            if self.repository.is_duplicate_news(article.get("url"), article.get("title"), article.get("source_name")):
                logger.info("[NEWS DEDUPE] duplicate skipped title=%s source=%s", article["title"], article.get("source_name"))
                continue
            self.repository.save_sent_news(
                NewsArticleRecord(
                    topic=topic,
                    article_hash=article_hash,
                    url=article.get("url"),
                    title=article["title"],
                    description=article.get("description"),
                    content=article.get("content"),
                    published_at=article.get("published_at"),
                    image=None,
                    source_name=article.get("source_name"),
                    source_url=article.get("source_url"),
                    status="new",
                    raw_payload=json.dumps(raw_article, ensure_ascii=False),
                )
            )
            new_articles.append(article)
        logger.info("[NEWS FETCH] topic=%s new_articles=%s", topic, len(new_articles))
        return FetchTopicResult(
            topic=topic,
            fetched_articles=fetch_result.fetched_articles,
            new_articles=new_articles,
            request_count_today=fetch_result.request_count_today,
            skipped_due_to_limit=fetch_result.skipped_due_to_limit,
        )
