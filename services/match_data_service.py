from __future__ import annotations

import base64
import json
from io import BytesIO
from typing import Any

from aiogram import types
from openai import AsyncOpenAI

from models.match_analysis import MatchAnalysisData


class MatchDataService:
    def __init__(self, client: AsyncOpenAI):
        self.client = client

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
            user_content.append({
                "type": "image_url",
                "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"},
            })
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
                        "Ничего не выдумывай."
                    ),
                },
                {"role": "user", "content": user_content},
            ],
        )
        content = response.choices[0].message.content or "{}"
        return json.loads(content)

    async def get_match_full_data(self, match_info: dict[str, Any]) -> MatchAnalysisData:
        response = await self.client.chat.completions.create(
            model="gpt-4o",
            temperature=0.3,
            max_tokens=2200,
            response_format={"type": "json_object"},
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Ты собираешь структурированный Match Center по футбольному матчу. "
                        "Используй только те данные, в которых уверен; если данных нет, ставь null, пустой список "
                        "или формулировки вида 'нет данных'/'Данные уточняются'. "
                        "Строго запрещено добавлять коэффициенты, ставки, советы по исходам и букмекерские рекомендации. "
                        "Верни только JSON c полями структуры MatchAnalysisData: "
                        "sport, league, match_time, home_team, away_team, home_away_label, source_hint, standings, motivation, "
                        "lineups, absences, lineup_impact_lines, lineup_impact_summary, form, h2h_lines, h2h_summary, home_away, "
                        "goal_trends, corners, cards, key_numbers, summary_lines, confidence_percent, time_to_start. "
                        "Для списков summary_lines верни 2 коротких аналитических абзаца без советов по ставкам."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        "Нормализованный матч для анализа:\n"
                        f"{json.dumps(match_info, ensure_ascii=False)}\n\n"
                        "Если часть данных недоступна, сохрани блок и поставь безопасный фолбэк."
                    ),
                },
            ],
        )
        payload = json.loads(response.choices[0].message.content or "{}")
        payload.setdefault("league", match_info.get("league") or "Турнир уточняется")
        payload.setdefault("home_team", match_info.get("home_team") or "Хозяева")
        payload.setdefault("away_team", match_info.get("away_team") or "Гости")
        payload.setdefault("match_time", match_info.get("match_datetime") or "Время уточняется")
        payload.setdefault("source_hint", match_info.get("source_hint") or "openai")
        return MatchAnalysisData.from_dict(payload)
