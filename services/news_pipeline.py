from __future__ import annotations

import logging
import os
from datetime import UTC, datetime
from typing import Any
from urllib import request
from urllib.error import URLError

from services.article_extractor import build_fallback_text, extract_article_text
from services.gnews_service import TOPICS
from services.news_repository import NewsRepository

logger = logging.getLogger(__name__)

MAX_POSTS_PER_TOPIC_PER_CYCLE = 3


class NewsPipeline:
    def __init__(
        self,
        *,
        bot,
        repository: NewsRepository,
        gnews_service,
        formatter,
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
        self.extract_enabled = os.getenv("ARTICLE_EXTRACT_ENABLED", "true").lower() == "true"
        self.extract_timeout = int(os.getenv("ARTICLE_EXTRACT_TIMEOUT", "15"))
        self.article_min_text_length = int(os.getenv("ARTICLE_MIN_TEXT_LENGTH", "800"))
        self.send_photo_enabled = os.getenv("SEND_PHOTO_ENABLED", "true").lower() == "true"

    def _target_chat_id(self) -> int | str | None:
        if self.news_post_mode == "channel":
            return self.news_channel_id
        return self.admin_id

    def _select_best_image(self, article: dict[str, Any], extraction: dict[str, Any]) -> str | None:
        return article.get("image") or extraction.get("top_image")

    def _is_valid_image_url(self, url: str | None) -> bool:
        if not url:
            return False
        try:
            req = request.Request(url, method="HEAD", headers={"User-Agent": "Mozilla/5.0 NewsBot/1.0"})
            with request.urlopen(req, timeout=10) as response:
                return (response.headers.get("Content-Type") or "").startswith("image/")
        except (URLError, Exception):  # noqa: BLE001
            return False

    async def send_news_to_telegram(self, article: dict[str, Any], messages: list[str], image_url: str | None) -> str:
        target_chat_id = self._target_chat_id()
        if target_chat_id is None:
            logger.error("[TELEGRAM SEND] target chat is not configured")
            return "failed"
        try:
            if self.send_photo_enabled and image_url and self._is_valid_image_url(image_url):
                await self.bot.send_photo(chat_id=target_chat_id, photo=image_url)
                logger.info("[TELEGRAM SEND] photo sent title=%s", article.get("title"))
            for chunk in messages:
                await self.bot.send_message(chat_id=target_chat_id, text=chunk, disable_web_page_preview=True)
            logger.info("[TELEGRAM SEND] message sent title=%s parts=%s", article.get("title"), len(messages))
            return "posted"
        except Exception as error:  # noqa: BLE001
            logger.error("[TELEGRAM SEND] failed title=%s error=%s", article.get("title"), error)
            return "failed"

    async def _prepare_article(self, article: dict[str, Any]) -> dict[str, Any]:
        extraction = {"success": False, "text": None, "top_image": None, "error": None, "method": "none"}
        if self.extract_enabled and article.get("url"):
            logger.info("[ARTICLE EXTRACT] start title=%s", article.get("title"))
            extraction = await extract_article_text(article["url"], timeout=self.extract_timeout, min_text_length=self.article_min_text_length)
            logger.info(
                "[ARTICLE EXTRACT] done title=%s success=%s method=%s error=%s",
                article.get("title"), extraction.get("success"), extraction.get("method"), extraction.get("error"),
            )
        article["final_text"] = build_fallback_text(article, extraction.get("text"), min_text_length=self.article_min_text_length)
        article["image_to_send"] = self._select_best_image(article, extraction)
        return article

    async def _publish_article(self, article: dict[str, Any]) -> str:
        prepared = await self._prepare_article(article)
        messages, translated_text = await self.formatter.format_post(prepared)
        status = await self.send_news_to_telegram(prepared, messages, prepared.get("image_to_send"))
        sent_at = datetime.now(UTC).isoformat() if status == "posted" else None
        self.repository.update_sent_status(prepared["article_hash"], status, translated_text=translated_text, sent_at=sent_at)
        return status

    async def run_single_topic_cycle(self, topic: str, *, trigger: str) -> str:
        result = await self.gnews_service.fetch_topic_news(topic)
        published = 0
        queued = 0
        for index, article in enumerate(result.new_articles):
            if index >= MAX_POSTS_PER_TOPIC_PER_CYCLE:
                self.repository.update_sent_status(article["article_hash"], "queued")
                queued += 1
                continue
            status = await self._publish_article(article)
            if status == "posted":
                published += 1
        self.repository.mark_last_fetch_time()
        return (
            f"topic={topic}; trigger={trigger}; fetched={result.fetched_articles}; "
            f"new={len(result.new_articles)}; posted={published}; queued={queued}; "
            f"today_requests={result.request_count_today}"
        )

    async def run_fetch_cycle(self, *, trigger: str) -> str:
        topic_rotation = os.getenv("NEWS_TOPIC_ROTATION", "true").lower() == "true"
        topics = list(TOPICS)
        if topic_rotation:
            topic = self.repository.get_next_topic(topics)
            summary = await self.run_single_topic_cycle(topic, trigger=trigger)
            return f"trigger={trigger}\n{summary}"
        lines = [f"trigger={trigger}"]
        for topic in topics:
            lines.append(await self.run_single_topic_cycle(topic, trigger=trigger))
        return "\n".join(lines)

    async def run_news_test(self, *, topic: str | None = None) -> str:
        test_topic = topic or self.repository.get_next_topic(list(TOPICS))
        return await self.run_single_topic_cycle(test_topic, trigger="news_test")
