from __future__ import annotations

from datetime import UTC, datetime

TOPIC_LABELS = {
    "football": "⚽ Football",
    "tennis": "🎾 Tennis",
    "hockey": "🏒 Hockey",
    "basketball": "🏀 Basketball",
}

TELEGRAM_MESSAGE_LIMIT = 4096
DEFAULT_ARTICLE_LIMIT = 8000


def _format_datetime(value: str | None) -> str:
    if not value:
        return "Unknown time"
    try:
        normalized = value.replace("Z", "+00:00")
        return datetime.fromisoformat(normalized).astimezone(UTC).strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return value


def format_news_message(article: dict, translated_article_text: str, article_limit: int = DEFAULT_ARTICLE_LIMIT) -> list[str]:
    body = (translated_article_text or "").strip()
    if article_limit > 0 and len(body) > article_limit:
        body = f"{body[:article_limit].rstrip()}…"

    header = (
        f"{TOPIC_LABELS.get(article.get('topic'), article.get('topic', 'News').title())}\n\n"
        f"📰 Заголовок: {article.get('title') or 'Untitled'}\n\n"
        f"🗞 Дереккөз: {article.get('source_name') or 'Unknown source'}\n"
        f"🕒 {_format_datetime(article.get('published_at'))}"
    ).strip()
    full_text = f"{header}\n\n{body}".strip()

    if len(full_text) <= TELEGRAM_MESSAGE_LIMIT:
        return [full_text]

    chunks: list[str] = []
    remaining = body
    first_room = TELEGRAM_MESSAGE_LIMIT - len(header) - 4
    chunks.append(f"{header}\n\n{remaining[:first_room].rstrip()}")
    remaining = remaining[first_room:].lstrip()
    while remaining:
        chunks.append(remaining[:TELEGRAM_MESSAGE_LIMIT].rstrip())
        remaining = remaining[TELEGRAM_MESSAGE_LIMIT:].lstrip()
    return chunks
