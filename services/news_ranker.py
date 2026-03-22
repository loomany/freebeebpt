from __future__ import annotations

from services.ai_news_processor import AINewsResult


class NewsRanker:
    def __init__(self, min_score: int = 75):
        self.min_score = min_score

    def should_send(self, result: AINewsResult | None) -> bool:
        if result is None:
            return False
        return result.is_important and result.importance_score >= self.min_score and result.importance_level in {"high", "top"}
