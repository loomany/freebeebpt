from __future__ import annotations

from services.formatter import TELEGRAM_MESSAGE_LIMIT, TOPIC_LABELS
from services.ai_news_processor import AINewsResult


def format_ai_news_message(topic: str, result: AINewsResult) -> list[str]:
    parts = [
        TOPIC_LABELS.get(topic, topic.title()),
        "",
        f"📰 {result.rewritten_title_kk or 'Жаңалық'}",
        "",
        (result.summary_kk or "").strip(),
    ]
    if result.key_points_kk:
        parts.extend([
            "",
            "🔹 Негізгісі:",
            *[f"• {point}" for point in result.key_points_kk],
        ])
    if result.betting_impact_kk:
        parts.extend(["", "📊 Ставкаға әсері:", result.betting_impact_kk.strip()])
    if result.team_impact_kk:
        parts.extend(["", "👥 Командаға әсері:", result.team_impact_kk.strip()])

    full_text = "\n".join(part for part in parts if part is not None).strip()
    if len(full_text) <= TELEGRAM_MESSAGE_LIMIT:
        return [full_text]

    chunks: list[str] = []
    remaining = full_text
    while remaining:
        chunks.append(remaining[:TELEGRAM_MESSAGE_LIMIT].rstrip())
        remaining = remaining[TELEGRAM_MESSAGE_LIMIT:].lstrip()
    return chunks
