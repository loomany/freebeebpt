from __future__ import annotations

import json
import logging
import os
import re
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

CATEGORY_KEYWORDS: dict[str, tuple[str, ...]] = {
    "basketball": (
        "nba", "wnba", "euroleague", "basketball", "fiba", "ncaa basketball", "march madness",
    ),
    "hockey": (
        "nhl", "khl", "hockey", "stanley cup", "ice hockey",
    ),
    "tennis": (
        "atp", "wta", "tennis", "grand slam", "us open", "wimbledon", "roland garros", "australian open",
    ),
    "football": (
        "football", "soccer", "premier league", "champions league", "uefa", "fifa", "la liga", "serie a", "bundesliga",
        "ligue 1", "mls", "fa cup",
    ),
}
VALID_CATEGORIES = frozenset(CATEGORY_KEYWORDS)
ENTITY_STOPWORDS = {
    "A", "An", "The", "And", "Or", "But", "For", "To", "From", "Of", "In", "On", "At", "By", "With",
    "After", "Before", "During", "Against", "Vs", "Via", "Is", "Are", "Was", "Were", "Be", "As",
    "Breaking", "Report", "Reports", "Update", "Live", "News",
}
TEAM_ENTITY_KEYWORDS = {
    "fc", "cf", "sc", "ac", "club", "team", "united", "city", "miami", "lakers", "celtics", "arsenal",
    "chelsea", "barcelona", "madrid", "yankees", "mets", "dodgers", "warriors", "heat", "inter", "real",
}
ENTITY_TOKEN_PATTERN = re.compile(r"[A-Z][a-z]+(?:[-'][A-Z]?[a-z]+)?|[A-Z]{2,}")
ENTITY_SEQUENCE_PATTERN = re.compile(
    r"\b(?:[A-Z][a-z]+(?:[-'][A-Z]?[a-z]+)?|[A-Z]{2,})(?:\s+(?:[A-Z][a-z]+(?:[-'][A-Z]?[a-z]+)?|[A-Z]{2,})){0,3}\b"
)
MATCHUP_PATTERN = re.compile(r"\b(?:vs\.?|versus|against)\b", re.IGNORECASE)


def _clean_prompt_fragment(value: str, *, limit: int = 220) -> str:
    cleaned = re.sub(r"\s+", " ", str(value or "")).strip(" .,:;!-")
    return cleaned[:limit].rstrip(" ,;:-")


def _looks_like_person_entity(entity: str) -> bool:
    tokens = entity.split()
    if len(tokens) < 2 or len(tokens) > 3:
        return False
    lowered = {token.lower().strip(".,") for token in tokens}
    if lowered & TEAM_ENTITY_KEYWORDS:
        return False
    if any(token.isupper() and len(token) <= 3 for token in tokens):
        return False
    if any(token.isupper() and len(token) > 3 for token in tokens):
        return False
    return True


def build_image_prompt_fallback(
    *,
    title: str,
    description: str,
    article_text: str,
    category: str,
    team_or_player_names: list[str] | None,
) -> tuple[str, str]:
    entities = [_clean_prompt_fragment(item, limit=80) for item in (team_or_player_names or []) if _clean_prompt_fragment(item, limit=80)]
    story_context = _clean_prompt_fragment(". ".join(part for part in [title, description] if part), limit=240)
    article_context = _clean_prompt_fragment(article_text, limit=240)
    context = story_context or article_context or "breaking sports news moment"
    sport_label = category or infer_news_category(title, description, article_text) or "sports"

    person_entity = next((entity for entity in entities if _looks_like_person_entity(entity)), None)
    matchup_detected = bool(MATCHUP_PATTERN.search(f"{title} {description} {article_text}"))

    if person_entity:
        prompt = (
            f"vertical sports poster, cinematic editorial style, {sport_label} scene, "
            f"featured athlete or coach inspired by {person_entity}, decisive news moment, {context}, "
            "dynamic action, dramatic stadium lighting, emotional realism, no text overlay, no logos, no watermarks, clean composition, suitable for 9:16"
        )
        return prompt, "person"

    if matchup_detected or entities:
        featured_entities = " vs ".join(entities[:2]) if len(entities) >= 2 else entities[0]
        prompt = (
            f"vertical sports poster, cinematic editorial style, {sport_label} match scene, "
            f"teams or rivalry inspired by {featured_entities}, decisive game atmosphere, {context}, "
            "crowd energy, dramatic motion, editorial realism, no text overlay, no logos, no watermarks, clean composition, suitable for 9:16"
        )
        return prompt, "team"

    prompt = (
        f"vertical sports poster, cinematic editorial style, {sport_label} editorial news scene, "
        f"dramatic sports atmosphere inspired by headline: {context}, intense action, stadium lights, "
        "editorial realism, no text overlay, no logos, no watermarks, clean composition, suitable for 9:16"
    )
    return prompt, "generic"


def _normalize_category(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    return normalized if normalized in VALID_CATEGORIES else ""


def infer_news_category(*values: Any) -> str | None:
    for value in values:
        normalized = _normalize_category(value)
        if normalized:
            return normalized

    haystack = " ".join(str(value or "") for value in values).lower()
    haystack = re.sub(r"[^a-z0-9#+]+", " ", haystack)
    for category, keywords in CATEGORY_KEYWORDS.items():
        if any(keyword in haystack for keyword in keywords):
            return category
    return None


def extract_team_or_player_names(*values: Any, limit: int = 8) -> list[str]:
    seen: set[str] = set()
    extracted: list[str] = []
    for value in values:
        text = str(value or "")
        if not text:
            continue
        for match in ENTITY_SEQUENCE_PATTERN.finditer(text):
            candidate = match.group(0).strip(" .,:;!?()[]{}\"'")
            if len(candidate) < 3:
                continue
            tokens = ENTITY_TOKEN_PATTERN.findall(candidate)
            if not tokens:
                continue
            if all(token in ENTITY_STOPWORDS for token in tokens):
                continue
            if len(tokens) == 1 and tokens[0] in ENTITY_STOPWORDS:
                continue
            normalized = " ".join(tokens)
            if normalized in seen:
                continue
            seen.add(normalized)
            extracted.append(normalized)
            if len(extracted) >= limit:
                return extracted
    return extracted


def ensure_ai_result_category(payload: dict[str, Any], *, topic: str, title: str, description: str, article_text: str, source_name: str, team_or_player_names: list[str] | None) -> dict[str, Any]:
    enriched_payload = dict(payload)
    explicit_category = _normalize_category(payload.get("category"))
    inferred_category = infer_news_category(
        title,
        description,
        article_text,
        source_name,
        " ".join(team_or_player_names or []),
        topic,
    )
    enriched_payload["category"] = explicit_category or inferred_category or ""
    fallback_prompt, fallback_mode = build_image_prompt_fallback(
        title=title,
        description=description,
        article_text=article_text,
        category=enriched_payload["category"],
        team_or_player_names=team_or_player_names,
    )
    logger.info("[AI PROMPT] mode=%s", fallback_mode)
    if not str(enriched_payload.get("image_prompt_en") or "").strip():
        enriched_payload["image_prompt_en"] = fallback_prompt
        logger.info("[AI PROMPT] fallback used")
    return enriched_payload


if PYDANTIC_AVAILABLE:

    class AINewsResult(BaseModel):
        is_important: bool
        importance_score: int = Field(ge=0, le=100)
        importance_level: str
        category: str = ""
        rewritten_title_kk: str = ""
        summary_kk: str = ""
        key_points_kk: list[str] = Field(default_factory=list)
        betting_impact_kk: str = ""
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

        @field_validator("category")
        @classmethod
        def validate_category(cls, value: str) -> str:
            normalized = _normalize_category(value)
            if value and not normalized:
                raise ValueError(f"category must be one of {sorted(VALID_CATEGORIES)}")
            return normalized

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
        category: str = ""
        rewritten_title_kk: str = ""
        summary_kk: str = ""
        key_points_kk: list[str] = field(default_factory=list)
        betting_impact_kk: str = ""
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
            self.category = _normalize_category(self.category)
            if self.category not in {"", *VALID_CATEGORIES}:
                raise ValueError("category must be football|tennis|hockey|basketball")
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
                parsed = AINewsResult.model_validate(
                    ensure_ai_result_category(
                        json.loads(response.output_text),
                        topic=topic,
                        title=title,
                        description=description,
                        article_text=article_text,
                        source_name=source_name,
                        team_or_player_names=team_or_player_names,
                    )
                )
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
