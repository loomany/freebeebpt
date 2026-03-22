from __future__ import annotations

import base64
import json
import logging
from io import BytesIO
from typing import Any

from aiogram import types
from openai import AsyncOpenAI

from models.match_analysis import MatchAnalysisData
from services.web_match_provider import WebMatchProvider

logger = logging.getLogger(__name__)

ALLOW_LLM_FOR_FACTS = False
MATCH_NOT_FOUND_TEXT = (
    "Не удалось найти достаточно данных по этому матчу. "
    "Попробуйте отправить более четкий скрин, где видны команды, лига и время матча."
)


class MatchDataService:
    def __init__(self, client: AsyncOpenAI, web_match_provider: WebMatchProvider):
        self.client = client
        self.web_match_provider = web_match_provider

    async def resolve_match_from_image(self, message: types.Message) -> dict[str, Any] | None:
        user_content: list[dict[str, Any]] = []
        user_text = (message.caption or message.text or "").strip()
        if user_text:
            user_content.append({"type": "text", "text": f"Дополнительный текст пользователя: {user_text}"})

        if message.photo:
            photo = message.photo[-1]
            buffer = BytesIO()
            await photo.download(destination=buffer)
            buffer.seek(0)
            img_b64 = base64.b64encode(buffer.read()).decode()
            user_content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"},
                }
            )
        elif user_text:
            user_content.append({"type": "text", "text": f"Матч из текста: {user_text}"})
        else:
            return None

        response = await self.client.chat.completions.create(
            model="gpt-4o",
            temperature=0.2,
            max_tokens=700,
            response_format={"type": "json_object"},
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Ты распознаёшь матч по скриншоту или тексту. Верни только JSON с полями: "
                        "sport, league, home_team, away_team, match_datetime, source_hint, confidence, tab_hint. "
                        "Если матч не удаётся определить уверенно, верни null в home_team или away_team. "
                        "Ничего не выдумывай. Нормализуй названия команд и турнира, но не генерируй статистику."
                    ),
                },
                {"role": "user", "content": user_content},
            ],
        )
        content = response.choices[0].message.content or "{}"
        payload = json.loads(content)
        payload.setdefault("source_hint", "openai-match-recognition")
        return payload

    def _build_missing_payload(self, match_info: dict[str, Any], source_hint: str, summary_lines: list[str]) -> MatchAnalysisData:
        payload = {
            "sport": match_info.get("sport") or "football",
            "league": match_info.get("league") or "Турнир уточняется",
            "home_team": match_info.get("home_team") or "Хозяева",
            "away_team": match_info.get("away_team") or "Гости",
            "match_time": match_info.get("match_datetime") or "Время уточняется",
            "source_hint": source_hint,
            "summary_lines": summary_lines,
            "confidence_percent": str(match_info.get("confidence") or "60"),
        }
        return MatchAnalysisData.from_dict(payload)

    async def get_match_full_data(self, match_info: dict[str, Any]) -> MatchAnalysisData:
        if ALLOW_LLM_FOR_FACTS:
            raise RuntimeError("ALLOW_LLM_FOR_FACTS must remain False for Match Center factual data")

        try:
            payload = await self.web_match_provider.build_match_analysis_data(match_info)
        except Exception as error:  # noqa: BLE001
            logger.warning("Web match provider failed: %s", error)
            payload = None

        if not payload:
            return self._build_missing_payload(
                match_info,
                source_hint="web-search-missing",
                summary_lines=[
                    MATCH_NOT_FOUND_TEXT,
                    "Блоки Match Center заполняются только подтверждёнными данными из открытых веб-источников.",
                ],
            )

        return MatchAnalysisData.from_dict(payload)
