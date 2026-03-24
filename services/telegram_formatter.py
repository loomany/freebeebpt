from __future__ import annotations

from services.formatter import TELEGRAM_MESSAGE_LIMIT, TOPIC_LABELS
from services.ai_news_processor import AINewsResult, infer_news_category


BETTING_IMPACT_LABEL_KK = "📊 Беттингке әсері:"


def _build_summary_fallback(result: AINewsResult, source_article: dict | None = None) -> str:
    summary = (result.summary_kk or "").strip()
    if summary:
        return summary
    if source_article:
        for key in ("description", "content", "final_text", "title"):
            value = (source_article.get(key) or "").strip()
            if value:
                return value[:700].rstrip()
    return "Толық мәтін қолжетімді болғанда жаңартамыз."


def format_ai_news_message(topic: str, result: AINewsResult, source_article: dict | None = None) -> list[str]:
    category = infer_news_category(result.category) or infer_news_category(topic)
    category_label = TOPIC_LABELS.get(category, (category or topic or "news").title())
    summary = _build_summary_fallback(result, source_article)
    parts = [
        category_label,
        "",
        f"📰 {result.rewritten_title_kk or 'Жаңалық'}",
        "",
        summary,
    ]
    if result.key_points_kk:
        parts.extend([
            "",
            "🔹 Негізгісі:",
            *[f"• {point}" for point in result.key_points_kk],
        ])
    if result.betting_impact_kk:
        parts.extend(["", BETTING_IMPACT_LABEL_KK, result.betting_impact_kk.strip()])

    full_text = "\n".join(part for part in parts if part is not None).strip()
    if len(full_text) <= TELEGRAM_MESSAGE_LIMIT:
        return [full_text]

    chunks: list[str] = []
    remaining = full_text
    while remaining:
        chunks.append(remaining[:TELEGRAM_MESSAGE_LIMIT].rstrip())
        remaining = remaining[TELEGRAM_MESSAGE_LIMIT:].lstrip()
    return chunks
