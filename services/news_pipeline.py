from __future__ import annotations

import json
import logging
import os
from datetime import UTC, datetime
from typing import Any

from keyboards import admin_news_review_keyboard
from services.article_extractor import build_fallback_text, extract_article_text
from services.ai_news_processor import extract_team_or_player_names
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
        ai_processor,
        ranker,
        telegram_publisher,
        fal_image_service,
        admin_id: int | None,
        news_channel_id: str | None,
        news_post_mode: str,
    ):
        self.bot = bot
        self.repository = repository
        self.gnews_service = gnews_service
        self.formatter = formatter
        self.ai_processor = ai_processor
        self.ranker = ranker
        self.telegram_publisher = telegram_publisher
        self.fal_image_service = fal_image_service
        self.admin_id = admin_id
        self.news_channel_id = news_channel_id
        self.news_post_mode = news_post_mode
        self.extract_enabled = os.getenv("ARTICLE_EXTRACT_ENABLED", "true").lower() == "true"
        self.extract_timeout = int(os.getenv("ARTICLE_EXTRACT_TIMEOUT", "15"))
        self.article_min_text_length = int(os.getenv("ARTICLE_MIN_TEXT_LENGTH", "800"))

    def _is_admin_review_mode(self) -> bool:
        return self.news_post_mode == "admin"

    def _target_chat_id(self) -> int | str | None:
        if self.news_post_mode == "channel":
            return self.news_channel_id
        return self.admin_id

    async def _prepare_article(self, article: dict[str, Any]) -> dict[str, Any]:
        extraction = {"success": False, "text": None, "error": None, "method": "none"}
        if self.extract_enabled and article.get("url"):
            logger.info("[ARTICLE EXTRACT] start title=%s", article.get("title"))
            extraction = await extract_article_text(article["url"], timeout=self.extract_timeout, min_text_length=self.article_min_text_length)
            logger.info(
                "[ARTICLE EXTRACT] done title=%s success=%s method=%s error=%s",
                article.get("title"), extraction.get("success"), extraction.get("method"), extraction.get("error"),
            )
        article["final_text"] = build_fallback_text(article, extraction.get("text"), min_text_length=self.article_min_text_length)
        return article

    async def _build_ai_result(self, article: dict[str, Any]):
        team_or_player_names = extract_team_or_player_names(
            article.get("title"),
            article.get("description"),
            article.get("content"),
            article.get("final_text"),
            article.get("source_name"),
        )
        return await self.ai_processor.process_news_with_ai(
            topic=article.get("topic") or "",
            title=article.get("title") or "",
            description=article.get("description") or "",
            article_text=article.get("final_text") or "",
            source_name=article.get("source_name") or "",
            published_at=article.get("published_at") or "",
            team_or_player_names=team_or_player_names,
            url=article.get("url"),
        )

    async def _generate_image(self, ai_result) -> str | None:
        if not ai_result or not ai_result.image_prompt_en:
            return None
        return await self.fal_image_service.generate_news_image(ai_result.image_prompt_en)

    async def send_article_to_channel(self, article_hash: str) -> str:
        article_state = self.repository.get_article_delivery_payload(article_hash)
        if not article_state:
            return "not_found"
        if article_state["sent_to_channel"]:
            return "already_sent"
        translated_text = (article_state.get("translated_text") or "").strip()
        if not translated_text:
            return "missing_text"
        publish_result = await self.telegram_publisher.publish_news_post(
            chat_id=self.news_channel_id,
            messages=[translated_text],
            image_url=article_state.get("generated_image_url"),
            article_title=article_state.get("title"),
        )
        if publish_result.status != "posted":
            return publish_result.error or "failed"
        self.repository.update_sent_status(
            article_hash,
            "posted",
            sent_at=datetime.now(UTC).isoformat(),
        )
        return "posted"

    async def _publish_article(self, article: dict[str, Any]) -> str:
        article_state = self.repository.get_article_state(article["article_hash"])
        if article_state and self.repository.should_skip_publication(article["article_hash"]):
            logger.info(
                "[SEND] duplicate skipped before publish title=%s status=%s sent_to_channel=%s",
                article.get("title"),
                article_state.get("status"),
                article_state.get("sent_to_channel"),
            )
            return "skipped"

        prepared = await self._prepare_article(article)
        ai_result = await self._build_ai_result(prepared)
        if not ai_result:
            self.repository.update_sent_status(
                prepared["article_hash"],
                "skipped",
            )
            return "skipped"

        messages, formatted_text = await self.formatter.format_post(prepared, ai_result)
        image_url = await self._generate_image(ai_result)
        admin_review_mode = self._is_admin_review_mode()
        should_send_to_target = admin_review_mode or self.ranker.should_send(ai_result)
        if not should_send_to_target:
            self.repository.update_sent_status(
                prepared["article_hash"],
                "skipped",
                translated_text=formatted_text,
                importance_score=ai_result.importance_score,
                importance_level=ai_result.importance_level,
                rewritten_title_kk=ai_result.rewritten_title_kk,
                summary_kk=ai_result.summary_kk,
                betting_impact_kk=ai_result.betting_impact_kk,
                image_prompt_en=ai_result.image_prompt_en,
                generated_image_url=image_url,
                send_reason=ai_result.send_reason,
                skip_reason=ai_result.skip_reason,
            )
            logger.info("[SEND] skipped title=%s", prepared.get("title"))
            return "skipped"

        target_chat_id = self._target_chat_id()
        reply_markup = None
        if admin_review_mode:
            reply_markup = admin_news_review_keyboard(prepared["article_hash"])
        publish_result = await self.telegram_publisher.publish_news_post(
            chat_id=target_chat_id,
            messages=messages,
            image_url=image_url,
            article_title=prepared.get("title"),
            reply_markup=reply_markup,
        )
        status = publish_result.status
        persisted_status = "review_pending" if admin_review_mode and status == "posted" else status
        if publish_result.error:
            logger.info("[SEND STATUS] title=%s chat_id=%s status=%s error=%s", prepared.get("title"), target_chat_id, status, publish_result.error)
        sent_at = datetime.now(UTC).isoformat() if status == "posted" and not admin_review_mode else None
        self.repository.update_sent_status(
            prepared["article_hash"],
            persisted_status,
            translated_text=formatted_text,
            sent_at=sent_at,
            importance_score=ai_result.importance_score,
            importance_level=ai_result.importance_level,
            rewritten_title_kk=ai_result.rewritten_title_kk,
            summary_kk=ai_result.summary_kk,
            betting_impact_kk=ai_result.betting_impact_kk,
            image_prompt_en=ai_result.image_prompt_en,
            generated_image_url=image_url,
            send_reason=ai_result.send_reason,
            skip_reason=ai_result.skip_reason,
        )
        return persisted_status

    async def run_single_topic_cycle(self, topic: str, *, trigger: str) -> str:
        result = await self.gnews_service.fetch_topic_news(topic)
        published = 0
        queued = 0
        skipped = 0
        max_posts = None if self._is_admin_review_mode() else MAX_POSTS_PER_TOPIC_PER_CYCLE
        for index, article in enumerate(result.new_articles):
            logger.info("[NEWS] fetched article topic=%s title=%s", topic, article.get("title"))
            if max_posts is not None and index >= max_posts:
                self.repository.update_sent_status(article["article_hash"], "queued")
                queued += 1
                continue
            status = await self._publish_article(article)
            if status in {"posted", "review_pending"}:
                published += 1
            elif status == "skipped":
                skipped += 1
        self.repository.mark_last_fetch_time()
        return (
            f"topic={topic}; trigger={trigger}; fetched={result.fetched_articles}; "
            f"new={len(result.new_articles)}; posted={published}; skipped={skipped}; queued={queued}; "
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

    async def _get_latest_prepared_article(self, topic: str | None = None) -> dict[str, Any] | None:
        test_topic = topic or self.repository.get_next_topic(list(TOPICS))
        result = await self.gnews_service.fetch_topic_news(test_topic)
        if not result.new_articles:
            return None
        return await self._prepare_article(result.new_articles[0])

    async def run_news_test(self, *, topic: str | None = None) -> str:
        test_topic = topic or self.repository.get_next_topic(list(TOPICS))
        return await self.run_single_topic_cycle(test_topic, trigger="news_test")

    async def run_news_test_ai(self, *, topic: str | None = None) -> str:
        article = await self._get_latest_prepared_article(topic)
        if not article:
            return "Нет свежих новостей для AI-теста"
        ai_result = await self._build_ai_result(article)
        if not ai_result:
            return "AI не вернул валидный JSON"
        messages, _ = await self.formatter.format_post(article, ai_result)
        payload = json.dumps(ai_result.model_dump(), ensure_ascii=False, indent=2)
        await self.bot.send_message(chat_id=self.admin_id, text=f"AI JSON:\n<pre>{payload[:3900]}</pre>", parse_mode="HTML")
        for chunk in messages:
            await self.bot.send_message(chat_id=self.admin_id, text=chunk, disable_web_page_preview=True)
        return f"AI test done: score={ai_result.importance_score}; level={ai_result.importance_level}; important={ai_result.is_important}"

    async def run_news_test_image(self, *, topic: str | None = None) -> str:
        article = await self._get_latest_prepared_article(topic)
        if not article:
            return "Нет свежих новостей для image-теста"
        ai_result = await self._build_ai_result(article)
        if not ai_result or not ai_result.image_prompt_en:
            return "AI не вернул image prompt"
        image_url = await self._generate_image(ai_result)
        if not image_url:
            return "fal не вернул картинку"
        await self.telegram_publisher.publish_news_post(
            chat_id=self.admin_id,
            messages=[f"Image prompt:\n{ai_result.image_prompt_en}"],
            image_url=image_url,
            article_title=article.get("title"),
        )
        return "Image test done"

    async def run_news_test_full(self, *, topic: str | None = None) -> str:
        article = await self._get_latest_prepared_article(topic)
        if not article:
            return "Нет свежих новостей для full-теста"
        ai_result = await self._build_ai_result(article)
        if not ai_result:
            return "AI full test failed"
        messages, _ = await self.formatter.format_post(article, ai_result)
        image_url = await self._generate_image(ai_result)
        publish_result = await self.telegram_publisher.publish_news_post(
            chat_id=self.admin_id,
            messages=messages,
            image_url=image_url,
            article_title=article.get("title"),
        )
        return f"Full test done: status={publish_result.status}; error={publish_result.error or 'none'}; score={ai_result.importance_score}; image={'yes' if image_url else 'no'}"

    async def run_news_test_raw(self, *, topic: str | None = None) -> str:
        article = await self._get_latest_prepared_article(topic)
        if not article:
            return "Нет свежих новостей для raw-теста"
        raw_preview = (article.get("final_text") or "")[:1500]
        return f"TITLE: {article.get('title')}\n\nRAW TEXT:\n{raw_preview}"

    async def run_news_test_compare(self, *, topic: str | None = None) -> str:
        article = await self._get_latest_prepared_article(topic)
        if not article:
            return "Нет свежих новостей для compare-теста"
        ai_result = await self._build_ai_result(article)
        if not ai_result:
            return "AI compare failed: invalid response"
        raw_preview = (article.get("final_text") or "")[:700]
        reason = ai_result.send_reason if self.ranker.should_send(ai_result) else ai_result.skip_reason
        return (
            f"original title: {article.get('title')}\n\n"
            f"original text short: {raw_preview}\n\n"
            f"AI summary: {ai_result.summary_kk}\n\n"
            f"image prompt: {ai_result.image_prompt_en}\n\n"
            f"importance score: {ai_result.importance_score}\n"
            f"importance level: {ai_result.importance_level}\n"
            f"why sent / skipped: {reason}"
        )
