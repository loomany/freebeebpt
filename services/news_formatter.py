from __future__ import annotations

from services.formatter import format_news_message
from services.translator import KazakhTranslator


class NewsFormatter:
    def __init__(self, client):
        self.translator = KazakhTranslator(client=client)

    async def format_post(self, article: dict) -> tuple[list[str], str]:
        translated_text = await self.translator.translate_to_kazakh(article.get("final_text") or "")
        return format_news_message(article, translated_text), translated_text
