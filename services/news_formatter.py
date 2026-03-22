from __future__ import annotations

from services.ai_news_processor import AINewsResult
from services.telegram_formatter import format_ai_news_message


class NewsFormatter:
    async def format_post(self, article: dict, ai_result: AINewsResult) -> tuple[list[str], str]:
        messages = format_ai_news_message(article.get("topic", "news"), ai_result)
        return messages, "\n\n".join(messages)
