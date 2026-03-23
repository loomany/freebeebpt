from __future__ import annotations

import logging
import os

from services.formatter import TELEGRAM_MESSAGE_LIMIT

logger = logging.getLogger(__name__)


class TelegramPublisher:
    def __init__(self, bot) -> None:
        self.bot = bot
        self.send_text_if_image_fail = os.getenv("SEND_TEXT_IF_IMAGE_FAIL", "true").lower() == "true"

    async def publish_news_post(
        self,
        *,
        chat_id: int | str | None,
        messages: list[str],
        image_url: str | None,
        article_title: str | None,
    ) -> str:
        if chat_id is None:
            logger.error("[SEND] target chat is not configured")
            return "failed"
        if not messages:
            logger.error("[SEND] empty message payload title=%s", article_title)
            return "failed"

        caption = messages[0]
        overflow_messages = messages[1:]
        sent_any = False

        if image_url:
            try:
                if len(caption) <= TELEGRAM_MESSAGE_LIMIT:
                    await self.bot.send_photo(chat_id=chat_id, photo=image_url, caption=caption)
                else:
                    await self.bot.send_photo(chat_id=chat_id, photo=image_url)
                    overflow_messages = messages
                logger.info("[SEND] photo success")
                sent_any = True
            except Exception as error:  # noqa: BLE001
                logger.warning("[SEND] photo failed title=%s error=%s", article_title, error)
                if not self.send_text_if_image_fail:
                    return "failed"
                overflow_messages = messages
                logger.info("[SEND] text fallback")
        else:
            logger.info("[SEND] text fallback")

        try:
            if not sent_any or overflow_messages:
                for chunk in overflow_messages:
                    await self.bot.send_message(chat_id=chat_id, text=chunk, disable_web_page_preview=True)
                sent_any = True
            return "posted" if sent_any else "failed"
        except Exception as error:  # noqa: BLE001
            logger.error("[SEND] failed title=%s error=%s", article_title, error)
            return "failed"
