from __future__ import annotations

import json
import logging
from typing import Any

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from openai import AsyncOpenAI

logger = logging.getLogger(__name__)

TOPIC_LABELS = {
    "football": "Football",
    "tennis": "Tennis",
    "hockey": "Hockey",
    "basketball": "Basketball",
}


class NewsFormatter:
    def __init__(self, client: "AsyncOpenAI" | None):
        self.client = client

    @staticmethod
    def _build_source_text(article: dict[str, Any]) -> str:
        source_name = article.get("source_name") or "Unknown source"
        url = article.get("url") or article.get("source_url") or ""
        return f"🔗 {source_name}\n{url}".strip()

    @staticmethod
    def _build_english_fallback(article: dict[str, Any]) -> str:
        text = article.get("title") or "News update"
        description = article.get("description") or ""
        if description and description != text:
            text = f"{text} {description}".strip()
        return text[:400]

    def _build_input_text(self, article: dict[str, Any]) -> str:
        title = article.get("title") or ""
        description = article.get("description") or ""
        content = article.get("content") or ""
        if content:
            return "\n".join(part for part in [title, description, content] if part).strip()
        if description:
            return "\n".join(part for part in [title, description] if part).strip()
        return title.strip()

    async def translate_news(self, article: dict[str, Any]) -> tuple[str | None, str | None]:
        if not self.client:
            return None, None

        source_text = self._build_input_text(article)
        try:
            response = await self.client.responses.create(
                model="gpt-4.1-mini",
                temperature=0.2,
                input=[
                    {
                        "role": "system",
                        "content": [
                            {
                                "type": "text",
                                "text": (
                                    "Translate sports news from English into Russian and Kazakh. "
                                    "Return strict JSON with keys ru_text and kk_text. "
                                    "Keep it short, factual, no analysis, no predictions, no betting, no invented details."
                                ),
                            }
                        ],
                    },
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": source_text,
                            }
                        ],
                    },
                ],
            )
            content = response.output_text
            payload = json.loads(content)
            return payload.get("ru_text"), payload.get("kk_text")
        except Exception as error:  # noqa: BLE001
            logger.error("[OPENAI ERROR] %s", error)
            return None, None

    async def format_post(self, article: dict[str, Any]) -> tuple[str, str | None, str | None]:
        ru_text, kk_text = await self.translate_news(article)
        if not ru_text or not kk_text:
            fallback = self._build_english_fallback(article)
            message = (
                f"📰 {TOPIC_LABELS.get(article['topic'], article['topic'].title())}\n\n"
                f"🇬🇧 {fallback}\n\n"
                f"{self._build_source_text(article)}"
            )
            return message, None, None

        message = (
            f"📰 {TOPIC_LABELS.get(article['topic'], article['topic'].title())}\n\n"
            f"🇷🇺 {ru_text}\n\n"
            f"🇰🇿 {kk_text}\n\n"
            f"{self._build_source_text(article)}"
        )
        return message, ru_text, kk_text
