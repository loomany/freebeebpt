from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING, Any

from prompts.news_prompt import NEWS_SYSTEM_PROMPT, build_news_user_prompt

try:
    from pydantic import BaseModel, Field, ValidationError, field_validator
    PYDANTIC_AVAILABLE = True
except ImportError:  # pragma: no cover
    BaseModel = object  # type: ignore[assignment]
    Field = None  # type: ignore[assignment]
    ValidationError = ValueError
    field_validator = None  # type: ignore[assignment]
    PYDANTIC_AVAILABLE = False

if TYPE_CHECKING:
    from openai import AsyncOpenAI

logger = logging.getLogger(__name__)

if PYDANTIC_AVAILABLE:

    class AINewsResult(BaseModel):
        is_important: bool
        importance_score: int = Field(ge=0, le=100)
        importance_level: str
        category: str
        rewritten_title_kk: str = ""
        summary_kk: str = ""
        key_points_kk: list[str] = Field(default_factory=list)
        betting_impact_kk: str = ""
        team_impact_kk: str = ""
        image_prompt_en: str = ""
        send_reason: str = ""
        skip_reason: str = ""

        @field_validator("importance_level")
        @classmethod
        def validate_level(cls, value: str) -> str:
            allowed = {"low", "medium", "high", "top"}
            if value not in allowed:
                raise ValueError(f"importance_level must be one of {sorted(allowed)}")
            return value

        @field_validator("key_points_kk")
        @classmethod
        def validate_key_points(cls, value: list[str]) -> list[str]:
            if len(value) > 3:
                raise ValueError("key_points_kk can contain at most 3 items")
            return [item.strip() for item in value if item and item.strip()]

        @field_validator("image_prompt_en")
        @classmethod
        def validate_image_prompt(cls, value: str, info) -> str:
            cleaned = (value or "").strip()
            if info.data.get("is_important") and not cleaned:
                raise ValueError("image_prompt_en is required when is_important is true")
            return cleaned

else:

    @dataclass(slots=True)
    class AINewsResult:
        is_important: bool
        importance_score: int
        importance_level: str
        category: str
        rewritten_title_kk: str = ""
        summary_kk: str = ""
        key_points_kk: list[str] = field(default_factory=list)
        betting_impact_kk: str = ""
        team_impact_kk: str = ""
        image_prompt_en: str = ""
        send_reason: str = ""
        skip_reason: str = ""

        @classmethod
        def model_validate(cls, payload: dict[str, Any]) -> "AINewsResult":
            result = cls(**payload)
            result._validate()
            return result

        def model_dump(self) -> dict[str, Any]:
            return asdict(self)

        def _validate(self) -> None:
            if not 0 <= int(self.importance_score) <= 100:
                raise ValueError("importance_score must be between 0 and 100")
            if self.importance_level not in {"low", "medium", "high", "top"}:
                raise ValueError("importance_level must be low|medium|high|top")
            if len(self.key_points_kk) > 3:
                raise ValueError("key_points_kk can contain at most 3 items")
            self.key_points_kk = [item.strip() for item in self.key_points_kk if item and item.strip()]
            self.image_prompt_en = (self.image_prompt_en or "").strip()
            if self.is_important and not self.image_prompt_en:
                raise ValueError("image_prompt_en is required when is_important is true")


class AINewsProcessor:
    def __init__(self, client: "AsyncOpenAI" | None):
        self.client = client
        self.enabled = os.getenv("OPENAI_ENABLED", "true").lower() == "true"
        self.model = os.getenv("OPENAI_MODEL", "gpt-5.4")
        self.max_retries = 1

    async def process_news_with_ai(
        self,
        *,
        topic: str,
        title: str,
        description: str,
        article_text: str,
        source_name: str,
        published_at: str,
        team_or_player_names: list[str] | None = None,
        url: str | None = None,
    ) -> AINewsResult | None:
        if not self.enabled or not self.client:
            logger.warning("[AI] skipped because OpenAI is disabled or client is missing")
            return None

        payload = {
            "topic": topic,
            "title": title,
            "description": description,
            "article_text": article_text,
            "source_name": source_name,
            "published_at": published_at,
            "team_or_player_names": team_or_player_names or [],
            "url": url or "",
        }
        logger.info("[AI] processing article title=%s source=%s", title, source_name)

        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                response = await self.client.responses.create(
                    model=self.model,
                    text={"format": {"type": "json_object"}},
                    input=[
                        {"role": "system", "content": NEWS_SYSTEM_PROMPT},
                        {"role": "user", "content": build_news_user_prompt(payload)},
                    ],
                )
                parsed = AINewsResult.model_validate(json.loads(response.output_text))
                logger.info("[AI] important=%s score=%s level=%s", parsed.is_important, parsed.importance_score, parsed.importance_level)
                logger.info("[AI] generated image prompt")
                logger.info("[AI] betting_impact=%s", "yes" if parsed.betting_impact_kk else "no")
                logger.info("[AI] translation=kk success")
                if not parsed.is_important:
                    logger.info("[AI] skipped reason=%s", parsed.skip_reason or "model marked as not important")
                return parsed
            except (ValidationError, json.JSONDecodeError, TypeError, ValueError) as error:
                last_error = error
                logger.warning("[AI] invalid JSON attempt=%s error=%s", attempt + 1, error)
            except Exception as error:  # noqa: BLE001
                last_error = error
                logger.warning("[AI] request failed attempt=%s error=%s", attempt + 1, error)
        logger.error("[AI] processing failed title=%s error=%s", title, last_error)
        return None


def serialize_ai_result(result: AINewsResult | None) -> dict[str, Any] | None:
    if result is None:
        return None
    return result.model_dump()
