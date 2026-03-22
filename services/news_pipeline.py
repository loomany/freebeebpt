from __future__ import annotations

import logging
import os
from datetime import UTC, datetime
from typing import Any

from services.gnews_service import GNewsService
from services.news_formatter import NewsFormatter
from services.news_repository import NewsRepository

logger = logging.getLogger(__name__)

MAX_POSTS_PER_TOPIC_PER_CYCLE = 3


class NewsPipeline:
    def __init__(
        self,
        *,
        bot,
        repository: NewsRepository,
        gnews_service: GNewsService,
        formatter: NewsFormatter,
        admin_id: int | None,
        news_channel_id: str | None,
        news_post_mode: str,
    ):
        self.bot = bot
        self.repository = repository
        self.gnews_service = gnews_service
        self.formatter = formatter
        self.admin_id = admin_id
        self.news_channel_id = news_channel_id
        self.news_post_mode = news_post_mode

    def _target_chat_id(self) -> int | str | None:
        if self.news_post_mode == "channel":
            return self.news_channel_id
        return self.admin_id

    async def _publish_article(self, article: dict[str, Any]) -> str:
        message, ru_text, kk_text = await self.formatter.format_post(article)
        target_chat_id = self._target_chat_id()
        if target_chat_id is None:
            logger.error("[TELEGRAM ERROR] target chat is not configured")
            self.repository.update_article_status(article["dedupe_key"], "failed", ru_text=ru_text, kk_text=kk_text)
            return "failed"
        try:
            await self.bot.send_message(chat_id=target_chat_id, text=message, disable_web_page_preview=False)
            self.repository.update_article_status(
                article["dedupe_key"],
                "posted",
                ru_text=ru_text,
                kk_text=kk_text,
                posted_at=datetime.now(UTC).isoformat(),
            )
            return "posted"
        except Exception as error:  # noqa: BLE001
            logger.error("[TELEGRAM ERROR] %s", error)
            self.repository.update_article_status(article["dedupe_key"], "failed", ru_text=ru_text, kk_text=kk_text)
            return "failed"

    async def run_single_topic_cycle(self, topic: str, *, trigger: str) -> str:
        result = await self.gnews_service.fetch_topic_news(topic)
        new_articles = result.new_articles
        published = 0
        queued = 0
        for index, article in enumerate(new_articles):
            if index >= MAX_POSTS_PER_TOPIC_PER_CYCLE:
                self.repository.update_article_status(article["dedupe_key"], "queued")
                queued += 1
                continue
            status = await self._publish_article(article)
            if status == "posted":
                published += 1
        self.repository.mark_last_fetch_time()
        return (
            f"topic={topic}; trigger={trigger}; fetched={result.fetched_articles}; "
            f"new={len(new_articles)}; posted={published}; queued={queued}; "
            f"today_requests={result.request_count_today}"
        )

    async def run_fetch_cycle(self, *, trigger: str) -> str:
        lines: list[str] = [f"trigger={trigger}"]
        for topic in ("football", "tennis", "hockey", "basketball"):
            lines.append(await self.run_single_topic_cycle(topic, trigger=trigger))
        return "\n".join(lines)
