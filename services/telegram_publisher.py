from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any

from aiogram.types import InlineKeyboardMarkup

from services.formatter import TELEGRAM_MESSAGE_LIMIT

logger = logging.getLogger(__name__)


@dataclass
class PublishResult:
    status: str
    error: str | None = None
    chat_id: int | str | None = None
    used_image: bool = False
    text_length: int = 0
    send_method: str | None = None
    message_id: int | None = None


class TelegramPublisher:
    def __init__(self, bot) -> None:
        self.bot = bot
        self.require_image_for_news_post = os.getenv("REQUIRE_IMAGE_FOR_NEWS_POST", "true").lower() == "true"
        self.send_text_if_image_fail = os.getenv("SEND_TEXT_IF_IMAGE_FAIL", "false").lower() == "true"

    @staticmethod
    def _format_error(error: Exception) -> str:
        return f"{error.__class__.__name__}: {error}"

    async def verify_channel_access(self, chat_id: int | str | None) -> tuple[bool, str]:
        if chat_id is None:
            message = "TELEGRAM_NEWS_CHAT_ID is not configured"
            logger.error("[CHANNEL CHECK] %s", message)
            return False, message

        try:
            chat = await self.bot.get_chat(chat_id)
            me = await self.bot.get_me()
            member = await self.bot.get_chat_member(chat_id, me.id)
            status = getattr(member, "status", "unknown")
            can_post_messages = getattr(member, "can_post_messages", None)
            logger.info(
                "[CHANNEL CHECK] chat_id=%s title=%s type=%s bot_status=%s can_post_messages=%s",
                chat_id,
                getattr(chat, "title", None),
                getattr(chat, "type", None),
                status,
                can_post_messages,
            )
            if status not in {"administrator", "creator"}:
                return False, f"bot_status={status}; bot must be administrator in the channel"
            if can_post_messages is False:
                return False, "bot is administrator but Post messages is disabled"
            return True, f"ok: bot_status={status}; can_post_messages={can_post_messages}"
        except Exception as error:  # noqa: BLE001
            error_text = self._format_error(error)
            logger.error("[CHANNEL CHECK] failed chat_id=%s error=%s", chat_id, error_text, exc_info=True)
            return False, error_text

    async def publish_news_post(
        self,
        *,
        chat_id: int | str | None,
        messages: list[str],
        image_url: str | None,
        article_title: str | None,
        reply_markup: InlineKeyboardMarkup | None = None,
    ) -> PublishResult:
        if chat_id is None:
            logger.error("[SEND] target chat is not configured")
            return PublishResult(status="failed", error="target chat is not configured")
        if not messages:
            logger.error("[SEND] empty message payload title=%s", article_title)
            return PublishResult(status="failed", error="empty message payload", chat_id=chat_id)

        caption = messages[0]
        overflow_messages = messages[1:]
        sent_any = False
        text_length = sum(len(chunk) for chunk in messages)
        logger.info(
            "[SEND DEBUG] title=%s chat_id=%s has_image=%s text_length=%s",
            article_title,
            chat_id,
            bool(image_url),
            text_length,
        )

        if image_url:
            try:
                if len(caption) <= TELEGRAM_MESSAGE_LIMIT:
                    response = await self.bot.send_photo(chat_id=chat_id, photo=image_url, caption=caption, reply_markup=reply_markup)
                else:
                    response = await self.bot.send_photo(chat_id=chat_id, photo=image_url, reply_markup=reply_markup)
                    overflow_messages = messages
                logger.info("[SEND RESULT] method=send_photo chat_id=%s response=%r", chat_id, response)
                sent_any = True
            except Exception as error:  # noqa: BLE001
                error_text = self._format_error(error)
                logger.warning(
                    "[SEND ERROR] method=send_photo title=%s chat_id=%s error=%s",
                    article_title,
                    chat_id,
                    error_text,
                    exc_info=True,
                )
                if self.require_image_for_news_post or not self.send_text_if_image_fail:
                    return PublishResult(
                        status="failed",
                        error=error_text,
                        chat_id=chat_id,
                        used_image=True,
                        text_length=text_length,
                        send_method="send_photo",
                    )
                overflow_messages = messages
                logger.info("[SEND] send_photo failed; switching to send_message fallback chat_id=%s", chat_id)
        else:
            if self.require_image_for_news_post:
                logger.error("[SEND] no image_url and image is required chat_id=%s title=%s", chat_id, article_title)
                return PublishResult(
                    status="failed",
                    error="image_url is required for news post",
                    chat_id=chat_id,
                    used_image=False,
                    text_length=text_length,
                    send_method="send_photo",
                )
            logger.info("[SEND] no image_url; using send_message chat_id=%s", chat_id)

        try:
            if not sent_any or overflow_messages:
                response: Any = None
                chunks_to_send = messages if not sent_any else overflow_messages
                for index, chunk in enumerate(chunks_to_send):
                    message_reply_markup = reply_markup if index == len(chunks_to_send) - 1 else None
                    response = await self.bot.send_message(
                        chat_id=chat_id,
                        text=chunk,
                        disable_web_page_preview=True,
                        reply_markup=message_reply_markup,
                    )
                logger.info("[SEND RESULT] method=send_message chat_id=%s response=%r", chat_id, response)
                sent_any = bool(chunks_to_send)
            return PublishResult(
                status="posted" if sent_any else "failed",
                chat_id=chat_id,
                used_image=bool(image_url),
                text_length=text_length,
                send_method="send_photo" if image_url and sent_any and not overflow_messages else "send_message",
                message_id=getattr(response, "message_id", None),
            )
        except Exception as error:  # noqa: BLE001
            error_text = self._format_error(error)
            logger.error(
                "[SEND ERROR] method=send_message title=%s chat_id=%s error=%s",
                article_title,
                chat_id,
                error_text,
                exc_info=True,
            )
            return PublishResult(
                status="failed",
                error=error_text,
                chat_id=chat_id,
                used_image=bool(image_url),
                text_length=text_length,
                send_method="send_message",
            )
