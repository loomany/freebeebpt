from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict
from datetime import UTC, datetime
from typing import Any

from keyboards import admin_news_review_keyboard
from services.article_extractor import build_fallback_text, extract_article_text
from services.ai_news_processor import extract_team_or_player_names
from services.gnews_service import TOPICS

logger = logging.getLogger(__name__)

MAX_POSTS_PER_TOPIC_PER_CYCLE = 3


class NewsPipeline:
    def __init__(
        self,
        *,
        bot,
        repository,
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
        self.manual_review_required = os.getenv("NEWS_MANUAL_REVIEW_REQUIRED", "true").lower() == "true"

    def _is_admin_review_mode(self) -> bool:
        return self.news_post_mode == "admin"

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

    async def _generate_image(self, ai_result) -> dict[str, Any]:
        if not ai_result or not ai_result.image_prompt_en:
            return {"request_id": None, "image_url": None, "fal_status": "skipped", "fal_error": "image_prompt_en is empty"}
        result = await self.fal_image_service.generate_news_image(ai_result.image_prompt_en)
        payload = asdict(result)
        return {
            "request_id": payload.get("request_id"),
            "image_url": payload.get("image_url"),
            "fal_status": payload.get("status"),
            "fal_error": payload.get("error"),
        }

    async def _notify_admin_image_failure(self, article: dict[str, Any], ai_result, fal_error: str | None, fal_status: str | None, fal_request_id: str | None) -> None:
        if not self.admin_id:
            return
        text = (
            "⚠️ Preview не собран: ошибка генерации изображения\n"
            f"Title: {article.get('title')}\n"
            f"Score: {ai_result.importance_score}\n"
            f"Level: {ai_result.importance_level}\n"
            f"Prompt exists: {'yes' if ai_result.image_prompt_en else 'no'}\n"
            f"fal_request_id: {fal_request_id or 'none'}\n"
            f"fal_status: {fal_status or 'unknown'}\n"
            f"fal_error: {fal_error or 'unknown'}"
        )
        try:
            await self.bot.send_message(chat_id=self.admin_id, text=text, disable_web_page_preview=True)
        except Exception:
            logger.exception("[ADMIN PREVIEW] debug notify failed article_hash=%s", article.get("article_hash"))

    async def _send_admin_preview(
        self,
        *,
        article: dict[str, Any],
        messages: list[str],
        formatted_text: str,
        image_url: str | None,
        fal_request_id: str | None,
        fal_status: str | None,
        fal_error: str | None,
    ) -> str:
        if not self.admin_id:
            logger.error("[ADMIN PREVIEW] send failed error=ADMIN_ID is not configured")
            self.repository.update_sent_status(article["article_hash"], "failed", fal_request_id=fal_request_id, fal_status=fal_status, fal_error="ADMIN_ID is not configured")
            return "failed"

        article_ref = self.repository.get_callback_article_ref(article["article_hash"])
        if not article_ref:
            logger.error("[ADMIN PREVIEW] send failed error=callback reference is not available article_hash=%s", article["article_hash"])
            self.repository.update_sent_status(
                article["article_hash"],
                "failed",
                translated_text=formatted_text,
                sent_to_admin=False,
                fal_request_id=fal_request_id,
                fal_status=fal_status,
                fal_error="callback reference is not available",
            )
            return "failed"

        reply_markup = admin_news_review_keyboard(article_ref)
        logger.info(
            "[ADMIN PREVIEW] send start admin_id=%s article_hash=%s article_ref=%s image=%s",
            self.admin_id,
            article["article_hash"],
            article_ref,
            bool(image_url),
        )
        publish_result = await self.telegram_publisher.publish_news_post(
            chat_id=self.admin_id,
            messages=messages,
            image_url=image_url,
            article_title=article.get("title"),
            reply_markup=reply_markup,
        )
        if publish_result.status != "posted":
            logger.error("[ADMIN PREVIEW] send failed error=%s article_hash=%s", publish_result.error, article["article_hash"])
            self.repository.update_sent_status(
                article["article_hash"],
                "failed",
                translated_text=formatted_text,
                sent_to_admin=False,
                fal_request_id=fal_request_id,
                fal_status=fal_status,
                fal_error=publish_result.error,
            )
            return "failed"

        logger.info("[ADMIN PREVIEW] send success message_id=%s article_hash=%s", getattr(publish_result, "message_id", None), article["article_hash"])
        self.repository.update_sent_status(
            article["article_hash"],
            "review_pending",
            translated_text=formatted_text,
            sent_to_admin=True,
            admin_message_id=getattr(publish_result, "message_id", None),
            fal_request_id=fal_request_id,
            fal_status=fal_status,
            fal_error=None,
        )
        return "review_pending"

    async def send_article_to_channel(self, article_hash: str) -> str:
        article_state = self.repository.get_article_delivery_payload(article_hash)
        if not article_state:
            return "not_found"
        if article_state["published_to_channel"] or article_state["sent_to_channel"]:
            return "already_sent"
        translated_text = (article_state.get("translated_text") or "").strip()
        if not translated_text:
            return "missing_text"
        image_url = article_state.get("generated_image_url")
        if not image_url:
            return "missing_image"
        logger.info("[PUBLISH] publish button clicked preview_id=%s", article_hash)
        publish_result = await self.telegram_publisher.publish_news_post(
            chat_id=self.news_channel_id,
            messages=[translated_text],
            image_url=image_url,
            article_title=article_state.get("title"),
        )
        if publish_result.status != "posted":
            logger.error("[PUBLISH] channel send failed preview_id=%s error=%s", article_hash, publish_result.error)
            return publish_result.error or "failed"
        self.repository.update_sent_status(
            article_hash,
            "posted",
            sent_at=datetime.now(UTC).isoformat(),
            published_to_channel=True,
            channel_message_id=getattr(publish_result, "message_id", None),
        )
        logger.info("[PUBLISH] channel send success preview_id=%s message_id=%s", article_hash, getattr(publish_result, "message_id", None))
        return "posted"

    async def skip_article_by_admin(self, article_hash: str) -> str:
        article_state = self.repository.get_article_delivery_payload(article_hash)
        if not article_state:
            return "not_found"
        if article_state.get("published_to_channel") or article_state.get("sent_to_channel"):
            return "already_sent"
        self.repository.update_sent_status(article_hash, "skipped_by_admin", skipped_by_admin=True)
        return "skipped"

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
        logger.info("[AI] processing title=%s", prepared.get("title"))
        ai_result = await self._build_ai_result(prepared)
        if not ai_result:
            self.repository.update_sent_status(prepared["article_hash"], "failed", skip_reason="ai_result_missing")
            return "failed"

        passed_for_admin_preview = self._is_admin_review_mode() or self.ranker.passed_for_admin_preview(ai_result)
        passed_for_auto_publish = self.ranker.passed_for_auto_publish(ai_result)
        logger.info(
            "[AI] result important=%s score=%s level=%s admin_threshold=%s passed_for_admin_preview=%s auto_publish_threshold=%s passed_for_auto_publish=%s",
            ai_result.is_important,
            ai_result.importance_score,
            ai_result.importance_level,
            self.ranker.admin_preview_min_score,
            passed_for_admin_preview,
            self.ranker.min_score,
            passed_for_auto_publish,
        )
        logger.info(
            "[ADMIN FILTER] score=%s threshold=%s passed=%s",
            ai_result.importance_score,
            self.ranker.admin_preview_min_score,
            passed_for_admin_preview,
        )
        logger.info(
            "[ADMIN FILTER] important=%s but allowed_for_preview=%s",
            ai_result.is_important,
            passed_for_admin_preview,
        )
        if not passed_for_admin_preview:
            self.repository.update_sent_status(
                prepared["article_hash"],
                "skipped",
                importance_score=ai_result.importance_score,
                importance_level=ai_result.importance_level,
                rewritten_title_kk=ai_result.rewritten_title_kk,
                summary_kk=ai_result.summary_kk,
                key_points_json=json.dumps(ai_result.key_points_kk, ensure_ascii=False),
                betting_impact_kk=ai_result.betting_impact_kk,
                image_prompt_en=ai_result.image_prompt_en,
                send_reason=ai_result.send_reason,
                skip_reason=ai_result.skip_reason or f"below_admin_preview_threshold:{self.ranker.admin_preview_min_score}",
            )
            logger.info("[SEND] skipped title=%s reason=%s", prepared.get("title"), ai_result.skip_reason or "below admin preview threshold")
            return "skipped"

        messages, formatted_text = await self.formatter.format_post(prepared, ai_result)
        fal_result = await self._generate_image(ai_result)
        image_url = fal_result["image_url"]
        fal_status = fal_result["fal_status"]
        fal_error = fal_result["fal_error"]
        fal_request_id = fal_result["request_id"]
        self.repository.update_sent_status(
            prepared["article_hash"],
            "image_ready" if image_url else "image_failed",
            translated_text=formatted_text,
            importance_score=ai_result.importance_score,
            importance_level=ai_result.importance_level,
            rewritten_title_kk=ai_result.rewritten_title_kk,
            summary_kk=ai_result.summary_kk,
            key_points_json=json.dumps(ai_result.key_points_kk, ensure_ascii=False),
            betting_impact_kk=ai_result.betting_impact_kk,
            image_prompt_en=ai_result.image_prompt_en,
            generated_image_url=image_url,
            fal_request_id=fal_request_id,
            fal_status=fal_status,
            fal_error=fal_error,
            send_reason=ai_result.send_reason,
            skip_reason=ai_result.skip_reason,
        )

        if not image_url:
            logger.error("[FAL] failed error=%s article_hash=%s", fal_error, prepared["article_hash"])
            await self._notify_admin_image_failure(prepared, ai_result, fal_error, fal_status, fal_request_id)
            return "image_failed"

        if not self._is_admin_review_mode() and not self.manual_review_required:
            publish_result = await self.telegram_publisher.publish_news_post(
                chat_id=self.news_channel_id,
                messages=messages,
                image_url=image_url,
                article_title=prepared.get("title"),
            )
            status = publish_result.status
            self.repository.update_sent_status(
                prepared["article_hash"],
                status,
                translated_text=formatted_text,
                sent_at=datetime.now(UTC).isoformat() if status == "posted" else None,
                published_to_channel=status == "posted",
            )
            return status

        return await self._send_admin_preview(
            article=prepared,
            messages=messages,
            formatted_text=formatted_text,
            image_url=image_url,
            fal_request_id=fal_request_id,
            fal_status=fal_status,
            fal_error=fal_error,
        )

    async def run_single_topic_cycle(self, topic: str, *, trigger: str) -> str:
        result = await self.gnews_service.fetch_topic_news(topic)
        published = 0
        queued = 0
        skipped = 0
        failed = 0
        for article in result.new_articles:
            logger.info("[NEWS] fetched article topic=%s title=%s", topic, article.get("title"))
            status = await self._publish_article(article)
            if status in {"posted", "review_pending"}:
                published += 1
            elif status == "skipped":
                skipped += 1
            else:
                failed += 1
        self.repository.mark_last_fetch_time()
        return (
            f"topic={topic}; trigger={trigger}; fetched={result.fetched_articles}; "
            f"new={len(result.new_articles)}; posted={published}; skipped={skipped}; failed={failed}; queued={queued}; "
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

    async def run_news_test_ai(self) -> str:
        article = await self._get_latest_prepared_article()
        if not article:
            return "Нет новой статьи для AI теста"
        ai_result = await self._build_ai_result(article)
        if not ai_result:
            return "AI не вернул результат"
        payload = json.dumps(ai_result.model_dump(), ensure_ascii=False, indent=2)
        await self.bot.send_message(chat_id=self.admin_id, text=f"AI JSON:\n<pre>{payload[:3900]}</pre>", parse_mode="HTML")
        return f"AI test done: score={ai_result.importance_score}; level={ai_result.importance_level}; important={ai_result.is_important}"

    async def run_news_test_image(self) -> str:
        article = await self._get_latest_prepared_article()
        if not article:
            return "Нет новой статьи для image теста"
        ai_result = await self._build_ai_result(article)
        if not ai_result:
            return "AI не вернул результат"
        fal_result = await self._generate_image(ai_result)
        image_url = fal_result["image_url"]
        fal_status = fal_result["fal_status"]
        fal_error = fal_result["fal_error"]
        fal_request_id = fal_result["request_id"]
        if not image_url:
            return f"fal не вернул картинку: {fal_error or fal_status}"
        await self.telegram_publisher.publish_news_post(
            chat_id=self.admin_id,
            messages=[f"Image prompt:\n{ai_result.image_prompt_en}"],
            image_url=image_url,
            article_title=article.get("title"),
        )
        return f"Image test done: image=yes status={fal_status}"

    async def run_news_test_full(self) -> str:
        article = await self._get_latest_prepared_article()
        if not article:
            return "Нет новой статьи для полного теста"
        status = await self._publish_article(article)
        return f"Full test done: status={status}"

    async def run_news_test_raw(self) -> str:
        article = await self._get_latest_prepared_article()
        if not article:
            return "Нет новой статьи"
        raw_preview = (article.get("final_text") or "")[:1500]
        return f"TITLE: {article.get('title')}\n\nRAW TEXT:\n{raw_preview}"

    async def run_news_test_compare(self) -> str:
        article = await self._get_latest_prepared_article()
        if not article:
            return "Нет новой статьи"
        ai_result = await self._build_ai_result(article)
        if not ai_result:
            return "AI не вернул результат"
        raw_preview = (article.get("final_text") or "")[:700]
        return (
            f"original text short: {raw_preview}\n\n"
            f"importance score: {ai_result.importance_score}\n"
            f"importance level: {ai_result.importance_level}\n"
            f"image prompt exists: {'yes' if ai_result.image_prompt_en else 'no'}"
        )

    async def get_last_debug_status(self) -> str:
        last = self.repository.get_last_article_debug()
        if not last:
            return "Нет сохранённых новостей"
        return "\n".join(
            [
                f"id={last.get('id')}",
                f"article_hash={last.get('article_hash')}",
                f"title={last.get('title')}",
                f"status={last.get('status')}",
                f"ai_score={last.get('importance_score')}",
                f"ai_level={last.get('importance_level')}",
                f"image_prompt_exists={'yes' if last.get('image_prompt_en') else 'no'}",
                f"image_url_exists={'yes' if last.get('generated_image_url') else 'no'}",
                f"sent_to_admin={bool(last.get('sent_to_admin'))}",
                f"admin_message_id={last.get('admin_message_id') or 'none'}",
                f"published_to_channel={bool(last.get('published_to_channel'))}",
                f"fal_request_id={last.get('fal_request_id') or 'none'}",
                f"fal_status={last.get('fal_status')}",
                f"fal_error={last.get('fal_error') or 'none'}",
                f"skip_reason={last.get('skip_reason') or 'none'}",
            ]
        )
