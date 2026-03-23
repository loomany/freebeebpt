from __future__ import annotations

from services.ai_news_processor import AINewsResult


class NewsRanker:
    def __init__(self, min_score: int = 75, admin_preview_min_score: int | None = None):
        self.min_score = min_score
        self.admin_preview_min_score = min_score if admin_preview_min_score is None else admin_preview_min_score

    def passed_for_admin_preview(self, result: AINewsResult | None) -> bool:
        if result is None:
            return False
        return result.importance_score >= self.admin_preview_min_score

    def passed_for_auto_publish(self, result: AINewsResult | None) -> bool:
        if result is None:
            return False
        return result.is_important and result.importance_score >= self.min_score and result.importance_level in {"high", "top"}

    def should_send(self, result: AINewsResult | None) -> bool:
        return self.passed_for_auto_publish(result)
