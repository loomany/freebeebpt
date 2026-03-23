from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict
from datetime import UTC, datetime
from typing import Any

from keyboards import admin_news_review_keyboard
from services.article_extractor import build_fallback_text, extract_article_text
from services.ai_news_processor import AINewsResult, build_image_prompt_fallback, extract_team_or_player_names
from services.gnews_service import TOPICS
from services.news_repository import NewsArticleRecord

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

    def _ensure_image_prompt(self, article: dict[str, Any], ai_result: AINewsResult) -> str:
        prompt = (ai_result.image_prompt_en or "").strip()
        if prompt:
            return prompt
        fallback_prompt, fallback_mode = build_image_prompt_fallback(
            title=article.get("title") or "",
            description=article.get("description") or "",
            article_text=article.get("final_text") or article.get("content") or "",
            category=ai_result.category or article.get("topic") or "",
            team_or_player_names=extract_team_or_player_names(
                article.get("title"),
                article.get("description"),
                article.get("content"),
                article.get("final_text"),
            ),
        )
        ai_result.image_prompt_en = fallback_prompt
        logger.info("[AI PROMPT] fallback rebuilt mode=%s article_hash=%s", fallback_mode, article.get("article_hash"))
        return fallback_prompt

    async def _generate_image(self, article: dict[str, Any], ai_result: AINewsResult) -> dict[str, Any]:
        prompt = self._ensure_image_prompt(article, ai_result)
        if not prompt:
            return {"request_id": None, "image_url": None, "fal_status": "skipped", "fal_error": "image_prompt_en is empty"}
        logger.info("[FAL] submit article_hash=%s source_type=%s", article.get("article_hash"), article.get("source_type", "gnews"))
        result = await self.fal_image_service.generate_news_image(prompt)
        payload = asdict(result)
        logger.info("[FAL] success article_hash=%s status=%s has_image=%s", article.get("article_hash"), payload.get("status"), bool(payload.get("image_url")))
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
            "⚠️ Preview собран без изображения\n"
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
            require_image=False,
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
            fal_error=fal_error,
        )
        return "review_pending"

    def build_manual_article(self, text: str) -> dict[str, Any]:
        normalized_text = (text or "").strip()
        if not normalized_text:
            raise ValueError("manual news text is empty")
        first_line = next((line.strip() for line in normalized_text.splitlines() if line.strip()), "")
        title = first_line[:180] if first_line else normalized_text[:180]
        published_at = datetime.now(UTC).isoformat()
        article_hash = self.repository.build_dedupe_key(None, f"manual::{title}::{published_at}", published_at, "manual_admin_input")
        article = {
            "topic": "manual",
            "article_hash": article_hash,
            "title": title or "Manual news",
            "description": "",
            "content": normalized_text,
            "final_text": normalized_text,
            "source_name": "manual_admin_input",
            "source_type": "manual",
            "published_at": published_at,
            "url": None,
            "image": None,
            "source_url": None,
            "raw_payload": json.dumps({"manual_input": normalized_text}, ensure_ascii=False),
        }
        logger.info("[MANUAL NEWS] article built title=%s article_hash=%s", article["title"], article_hash)
        return article

    async def process_manual_news(self, text: str) -> str:
        logger.info("[MANUAL NEWS] text received length=%s", len((text or "").strip()))
        article = self.build_manual_article(text)
        self.repository.save_sent_news(
            NewsArticleRecord(
                topic=article["topic"],
                article_hash=article["article_hash"],
                url=article["url"],
                title=article["title"],
                description=article["description"],
                content=article["content"],
                published_at=article["published_at"],
                image=article["image"],
                source_name=article["source_name"],
                source_url=article["source_url"],
                source_type="manual",
                raw_payload=article["raw_payload"],
            )
        )
        return await self._publish_article(article, force_preview=True)

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
        logger.info("[PUBLISH] publish button clicked preview_id=%s source_type=%s", article_hash, article_state.get("source_type", "gnews"))
        publish_result = await self.telegram_publisher.publish_news_post(
            chat_id=self.news_channel_id,
            messages=[translated_text],
            image_url=image_url,
            article_title=article_state.get("title"),
            require_image=False,
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
        log_label = "manual preview published" if article_state.get("source_type") == "manual" else "channel send success"
        logger.info("[PUBLISH] %s preview_id=%s message_id=%s", log_label, article_hash, getattr(publish_result, "message_id", None))
        return "posted"

    async def skip_article_by_admin(self, article_hash: str) -> str:
        article_state = self.repository.get_article_delivery_payload(article_hash)
        if not article_state:
            return "not_found"
        if article_state.get("published_to_channel") or article_state.get("sent_to_channel"):
            return "already_sent"
        self.repository.update_sent_status(article_hash, "skipped_by_admin", skipped_by_admin=True)
        log_label = "manual preview skipped" if article_state.get("source_type") == "manual" else "preview skipped"
        logger.info("[SKIP] %s preview_id=%s", log_label, article_hash)
        return "skipped"

    async def _publish_article(self, article: dict[str, Any], *, force_preview: bool = False) -> str:
        article_state = self.repository.get_article_state(article["article_hash"])
        if not force_preview and article_state and self.repository.should_skip_publication(article["article_hash"]):
            logger.info(
                "[SEND] duplicate skipped before publish title=%s status=%s sent_to_channel=%s",
                article.get("title"),
                article_state.get("status"),
                article_state.get("sent_to_channel"),
            )
            return "skipped"

        prepared = await self._prepare_article(article)
        logger.info("[AI] processing %s title=%s", "manual news" if force_preview else "title", prepared.get("title"))
        ai_result = await self._build_ai_result(prepared)
        if not ai_result:
            self.repository.update_sent_status(prepared["article_hash"], "failed", skip_reason="ai_result_missing")
            return "failed"

        passed_for_admin_preview = force_preview or self._is_admin_review_mode() or self.ranker.passed_for_admin_preview(ai_result)
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
        if force_preview:
            logger.info("[ADMIN FILTER] manual input forced to preview article_hash=%s", prepared["article_hash"])
        else:
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
                image_prompt_en=self._ensure_image_prompt(prepared, ai_result),
                send_reason=ai_result.send_reason,
                skip_reason=ai_result.skip_reason or f"below_admin_preview_threshold:{self.ranker.admin_preview_min_score}",
            )
            logger.info("[SEND] skipped title=%s reason=%s", prepared.get("title"), ai_result.skip_reason or "below admin preview threshold")
            return "skipped"

        messages, formatted_text = await self.formatter.format_post(prepared, ai_result)
        fal_result = await self._generate_image(prepared, ai_result)
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

        if not force_preview and not self._is_admin_review_mode() and not self.manual_review_required and image_url:
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
        article = result.new_articles[0]
        return await self._prepare_article(article)

    async def run_news_test(self) -> str:
        article = await self._get_latest_prepared_article()
        if not article:
            return "Нет новых статей для теста"
        return await self._publish_article(article)

    async def run_news_test_ai(self) -> str:
        article = await self._get_latest_prepared_article()
        if not article:
            return "Нет новых статей для AI-теста"
        ai_result = await self._build_ai_result(article)
        payload = json.dumps(ai_result.model_dump(), ensure_ascii=False, indent=2) if ai_result else "AI result is empty"
        await self.bot.send_message(chat_id=self.admin_id, text=f"AI JSON:\n<pre>{payload[:3900]}</pre>", parse_mode="HTML")
        return "AI test done"

    async def run_news_test_image(self) -> str:
        article = await self._get_latest_prepared_article()
        if not article:
            return "Нет новых статей для image-теста"
        ai_result = await self._build_ai_result(article)
        if not ai_result:
            return "AI result is empty"
        fal_result = await self._generate_image(article, ai_result)
        image_url = fal_result["image_url"]
        fal_status = fal_result["fal_status"]
        fal_error = fal_result["fal_error"]
        fal_request_id = fal_result["request_id"]
        if not image_url:
            return f"fal не вернул картинку: {fal_error or fal_status}"
        await self.telegram_publisher.publish_news_post(
            chat_id=self.admin_id,
            messages=[f"fal_request_id={fal_request_id or 'none'}\nstatus={fal_status}"],
            image_url=image_url,
            article_title=article.get("title"),
            require_image=False,
        )
        return f"Image test done: image=yes status={fal_status}"

    async def run_news_test_full(self) -> str:
        article = await self._get_latest_prepared_article()
        if not article:
            return "Нет новых статей для полного теста"
        status = await self._publish_article(article)
        return f"Full test done: {status}"

    async def run_news_test_raw(self) -> str:
        article = await self._get_latest_prepared_article()
        if not article:
            return "Нет новых статей для raw-теста"
        raw_preview = (article.get("final_text") or "")[:1500]
        return f"TITLE: {article.get('title')}\n\nRAW TEXT:\n{raw_preview}"

    async def run_news_test_compare(self) -> str:
        article = await self._get_latest_prepared_article()
        if not article:
            return "Нет новых статей для compare-теста"
        raw_preview = (article.get("final_text") or "")[:700]
        ai_result = await self._build_ai_result(article)
        formatted_preview = "\n\n".join((await self.formatter.format_post(article, ai_result))[0]) if ai_result else "AI result empty"
        return (
            f"original text short: {raw_preview}\n\n"
            f"formatted:\n{formatted_preview[:1500]}"
        )

    async def get_last_debug_status(self) -> str:
        last = self.repository.get_last_article_debug()
        if not last:
            return "Нет сохранённых preview"
        return "\n".join(
            [
                f"id={last.get('id')}",
                f"article_hash={last.get('article_hash')}",
                f"topic={last.get('topic')}",
                f"source_type={last.get('source_type') or 'gnews'}",
                f"title={last.get('title')}",
                f"status={last.get('status')}",
                f"importance_score={last.get('importance_score')}",
                f"importance_level={last.get('importance_level')}",
                f"sent_to_admin={bool(last.get('sent_to_admin'))}",
                f"admin_message_id={last.get('admin_message_id') or 'none'}",
                f"published_to_channel={bool(last.get('published_to_channel'))}",
                f"fal_request_id={last.get('fal_request_id') or 'none'}",
                f"fal_status={last.get('fal_status')}",
                f"fal_error={last.get('fal_error') or 'none'}",
                f"send_reason={last.get('send_reason') or 'none'}",
                f"skip_reason={last.get('skip_reason') or 'none'}",
                f"created_at={last.get('created_at')}",
                f"updated_at={last.get('updated_at')}",
            ]
        )
