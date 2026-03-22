from __future__ import annotations

import base64
import json
import logging
from io import BytesIO
from typing import Any

from aiogram import types
from openai import AsyncOpenAI

from models.match_analysis import MatchAnalysisData
from services.sports_provider import SportsProvider, SportsProviderError

logger = logging.getLogger(__name__)

ALLOW_LLM_FOR_FACTS = False


class MatchDataService:
    def __init__(self, client: AsyncOpenAI, sports_provider: SportsProvider):
        self.client = client
        self.sports_provider = sports_provider

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

    def _log_block_source(self, block_name: str, has_data: bool) -> None:
        logger.info("[DATA SOURCE] %s = %s", block_name, "OK" if has_data else "EMPTY")

    @staticmethod
    def _make_team_standing(entry: dict[str, Any] | None) -> dict[str, str]:
        if not entry:
            return {}
        all_stats = entry.get("all") or {}
        goals = all_stats.get("goals") or {}
        return {
            "position": str(entry.get("rank") or "нет данных"),
            "played": str(all_stats.get("played") or "нет данных"),
            "wins": str(all_stats.get("win") or "нет данных"),
            "draws": str(all_stats.get("draw") or "нет данных"),
            "losses": str(all_stats.get("lose") or "нет данных"),
            "points": str(entry.get("points") or "нет данных"),
            "goals_for": str(goals.get("for") or "нет данных"),
            "goals_against": str(goals.get("against") or "нет данных"),
        }

    @staticmethod
    def _build_form_block(matches: list[dict[str, Any]], team_id: int) -> dict[str, str]:
        if not matches:
            return {}
        icons: list[str] = []
        goals_for = 0
        goals_against = 0
        for match in matches[:5]:
            teams = match.get("teams") or {}
            home = teams.get("home") or {}
            away = teams.get("away") or {}
            goals = match.get("goals") or {}
            is_home = home.get("id") == team_id
            team_goals = goals.get("home") if is_home else goals.get("away")
            opp_goals = goals.get("away") if is_home else goals.get("home")
            if team_goals is None or opp_goals is None:
                continue
            goals_for += int(team_goals)
            goals_against += int(opp_goals)
            icons.append("W" if team_goals > opp_goals else "D" if team_goals == opp_goals else "L")
        if not icons:
            return {}
        return {
            "icons": " ".join(icons),
            "goals_for": str(goals_for),
            "goals_against": str(goals_against),
        }

    @staticmethod
    def _build_lineups(lineups: list[dict[str, Any]], home_id: int, away_id: int) -> dict[str, Any]:
        result: dict[str, Any] = {"home": {}, "away": {}}
        for lineup in lineups:
            team = lineup.get("team") or {}
            players = []
            for start_xi in lineup.get("startXI") or []:
                player = start_xi.get("player") or {}
                number = player.get("number")
                name = player.get("name") or player.get("lastname")
                if name:
                    players.append(f"{number}. {name}" if number else str(name))
            team_payload = {
                "formation": lineup.get("formation") or "Схема уточняется",
                "players": players,
            }
            if team.get("id") == home_id:
                result["home"] = team_payload
            elif team.get("id") == away_id:
                result["away"] = team_payload
        return result

    @staticmethod
    def _build_absence_lines(injuries: list[dict[str, Any]]) -> list[str]:
        lines: list[str] = []
        for item in injuries[:5]:
            player = item.get("player") or {}
            reason = item.get("type") or item.get("reason") or "статус уточняется"
            name = player.get("name") or player.get("lastname")
            if name:
                lines.append(f"{name} — {reason}")
        return lines

    @staticmethod
    def _build_h2h_lines(matches: list[dict[str, Any]]) -> list[str]:
        lines: list[str] = []
        for match in matches[:5]:
            teams = match.get("teams") or {}
            goals = match.get("goals") or {}
            fixture = match.get("fixture") or {}
            home_name = (teams.get("home") or {}).get("name") or "Хозяева"
            away_name = (teams.get("away") or {}).get("name") or "Гости"
            score = f"{goals.get('home', '-')}-{goals.get('away', '-')}"
            date = str(fixture.get("date") or "")[:10]
            lines.append(f"{date}: {home_name} {score} {away_name}")
        return lines

    @staticmethod
    def _build_home_away_lines(home_stats: dict[str, Any], away_stats: dict[str, Any]) -> dict[str, list[str]]:
        fixtures_home = (home_stats.get("fixtures") or {}).get("wins") or {}
        fixtures_away = (away_stats.get("fixtures") or {}).get("wins") or {}
        home_line = f"Победы дома: {(fixtures_home.get('home') if fixtures_home else None) or 'нет данных'}"
        away_line = f"Победы в гостях: {(fixtures_away.get('away') if fixtures_away else None) or 'нет данных'}"
        return {"home_lines": [home_line], "away_lines": [away_line]}

    @staticmethod
    def _build_goal_trends(home_stats: dict[str, Any], away_stats: dict[str, Any]) -> list[str]:
        trends: list[str] = []
        for label, stats in (("Хозяева", home_stats), ("Гости", away_stats)):
            goals_for = (((stats.get("goals") or {}).get("for") or {}).get("average") or {}).get("total")
            goals_against = (((stats.get("goals") or {}).get("against") or {}).get("average") or {}).get("total")
            if goals_for:
                trends.append(f"{label}: в среднем забивают {goals_for} гола")
            if goals_against:
                trends.append(f"{label}: в среднем пропускают {goals_against} гола")
        return trends

    @staticmethod
    def _build_corners(match_context: dict[str, Any], home_name: str, away_name: str) -> dict[str, Any]:
        statistics = match_context.get("statistics") or []
        home_stat = "Данные уточняются"
        away_stat = "Данные уточняются"
        trends: list[str] = []
        for stat_block in statistics:
            team = (stat_block.get("team") or {}).get("name")
            stats = stat_block.get("statistics") or []
            corners = next((item.get("value") for item in stats if item.get("type") == "Corner Kicks"), None)
            if corners is not None:
                line = f"угловые за матч: {corners}"
                if team == home_name:
                    home_stat = line
                elif team == away_name:
                    away_stat = line
        if home_stat != "Данные уточняются" or away_stat != "Данные уточняются":
            trends.append("Статистика угловых взята из match context API")
        return {"home_stat": home_stat, "away_stat": away_stat, "trends": trends}

    @staticmethod
    def _build_cards(match_context: dict[str, Any], home_name: str, away_name: str, referee: str | None) -> dict[str, Any]:
        statistics = match_context.get("statistics") or []
        home_stat = "Данные уточняются"
        away_stat = "Данные уточняются"
        trends: list[str] = []
        for stat_block in statistics:
            team = (stat_block.get("team") or {}).get("name")
            stats = stat_block.get("statistics") or []
            yellow = next((item.get("value") for item in stats if item.get("type") == "Yellow Cards"), None)
            if yellow is not None:
                line = f"жёлтые карточки за матч: {yellow}"
                if team == home_name:
                    home_stat = line
                elif team == away_name:
                    away_stat = line
        if home_stat != "Данные уточняются" or away_stat != "Данные уточняются":
            trends.append("Данные по карточкам получены из match context API")
        return {
            "home_stat": home_stat,
            "away_stat": away_stat,
            "referee_line": referee or "Данные по судье уточняются",
            "trends": trends,
        }

    @staticmethod
    def _build_key_numbers(home_stats: dict[str, Any], away_stats: dict[str, Any]) -> dict[str, str]:
        def avg(stats: dict[str, Any], side: str) -> str:
            value = (((stats.get("goals") or {}).get(side) or {}).get("average") or {}).get("total")
            return str(value) if value else "нет данных"

        return {
            "home_avg_goals": avg(home_stats, "for"),
            "away_avg_goals": avg(away_stats, "for"),
            "home_avg_conceded": avg(home_stats, "against"),
            "away_avg_conceded": avg(away_stats, "against"),
        }

    @staticmethod
    def _build_motivation(standings_home: dict[str, str], standings_away: dict[str, str]) -> dict[str, Any]:
        def lines(team: dict[str, str]) -> tuple[list[str], str]:
            position = team.get("position", "нет данных")
            points = team.get("points", "нет данных")
            return [f"Текущая позиция: {position}", f"Очки: {points}"], "Оценка мотивации опирается на текущее место в таблице."

        home_lines, home_summary = lines(standings_home)
        away_lines, away_summary = lines(standings_away)
        return {
            "home_lines": home_lines,
            "away_lines": away_lines,
            "home_summary": home_summary,
            "away_summary": away_summary,
        }

    @staticmethod
    def _build_time_to_start(fixture_date: str | None) -> str:
        if not fixture_date:
            return "уточняется"
        normalized = fixture_date.replace("Z", "+00:00")
        try:
            delta = datetime.fromisoformat(normalized) - datetime.now(datetime.fromisoformat(normalized).tzinfo)
        except ValueError:
            return "уточняется"
        total_hours = int(delta.total_seconds() // 3600)
        if total_hours < 0:
            return "матч уже начался или завершился"
        days, hours = divmod(total_hours, 24)
        return f"{days}д {hours}ч"

    def _build_missing_payload(self, match_info: dict[str, Any], source_hint: str, summary_lines: list[str]) -> MatchAnalysisData:
        payload = {
            "league": match_info.get("league") or "Турнир уточняется",
            "home_team": match_info.get("home_team") or "Хозяева",
            "away_team": match_info.get("away_team") or "Гости",
            "match_time": match_info.get("match_datetime") or "Время уточняется",
            "source_hint": source_hint,
            "summary_lines": summary_lines,
        }
        for block in ("standings", "lineups", "injuries", "h2h", "form", "stats", "referee", "cards", "corners"):
            self._log_block_source(block, False)
        return MatchAnalysisData.from_dict(payload)

    @staticmethod
    def _build_summary_lines(match_info: dict[str, Any], standings_home: dict[str, str], standings_away: dict[str, str], h2h_lines: list[str]) -> list[str]:
        home_team = match_info.get("home_team") or "Хозяева"
        away_team = match_info.get("away_team") or "Гости"
        first = (
            f"{home_team} подходит к матчу с позицией {standings_home.get('position', 'нет данных')} в таблице, "
            f"а {away_team} занимает {standings_away.get('position', 'нет данных')} место."
        )
        second = (
            "Личные встречи и текущая форма учтены только в пределах реально полученных API-данных."
            if h2h_lines else
            "Часть аналитики ограничена: по личным встречам доступно недостаточно подтверждённых данных."
        )
        return [first, second]

    async def get_match_full_data(self, match_info: dict[str, Any]) -> MatchAnalysisData:
        if ALLOW_LLM_FOR_FACTS:
            raise RuntimeError("ALLOW_LLM_FOR_FACTS must remain False for Match Center factual data")

        self.sports_provider.last_api_call_attempted = False

        try:
            status_payload = await self.sports_provider.check_api_status()
            if not status_payload:
                logger.warning("Sports API status returned empty payload")
            fixture = await self.sports_provider.find_fixture(
                home_team=match_info.get("home_team") or "",
                away_team=match_info.get("away_team") or "",
                date=match_info.get("match_datetime"),
            )
        except SportsProviderError as error:
            logger.warning("Sports provider unavailable: %s", error)
            return self._build_missing_payload(
                match_info,
                source_hint="sports-api-unavailable",
                summary_lines=[
                    "Sports API недоступен или не настроен, поэтому Match Center не подменяет факты генерацией.",
                    "Показываются только безопасные fallback-блоки до появления подтверждённых спортивных данных.",
                ],
            )

        if not fixture:
            if not self.sports_provider.last_api_call_attempted:
                raise RuntimeError("Sports API fallback triggered before any API request was attempted")
            logger.warning("Fixture not found for %s vs %s", match_info.get("home_team"), match_info.get("away_team"))
            return self._build_missing_payload(
                match_info,
                source_hint="sports-api-missing-fixture",
                summary_lines=[
                    "Матч распознан, но точный fixture в спортивном API пока не найден.",
                    "Структура Match Center сохранена, однако фактические блоки заполняются только подтверждёнными данными.",
                ],
            )

        league = fixture.get("league") or {}
        teams = fixture.get("teams") or {}
        home_team = teams.get("home") or {}
        away_team = teams.get("away") or {}
        fixture_data = fixture.get("fixture") or {}
        league_id = league.get("id")
        season = league.get("season")
        fixture_id = fixture_data.get("id")
        home_team_id = home_team.get("id")
        away_team_id = away_team.get("id")

        try:
            standings_rows = await self.sports_provider.get_standings(league_id, season) if league_id and season else []
            lineups = await self.sports_provider.get_lineups(fixture_id) if fixture_id else []
            home_injuries = await self.sports_provider.get_injuries(home_team_id, league_id, season) if home_team_id and league_id and season else []
            away_injuries = await self.sports_provider.get_injuries(away_team_id, league_id, season) if away_team_id and league_id and season else []
            h2h = await self.sports_provider.get_h2h(home_team_id, away_team_id) if home_team_id and away_team_id else []
            home_form = await self.sports_provider.get_team_form(home_team_id, league_id, season) if home_team_id and league_id and season else []
            away_form = await self.sports_provider.get_team_form(away_team_id, league_id, season) if away_team_id and league_id and season else []
            home_stats = await self.sports_provider.get_team_stats(home_team_id, league_id, season) if home_team_id and league_id and season else {}
            away_stats = await self.sports_provider.get_team_stats(away_team_id, league_id, season) if away_team_id and league_id and season else {}
            match_context = await self.sports_provider.get_match_context(fixture_id) if fixture_id else {}
            referee = await self.sports_provider.get_referee(fixture_id) if fixture_id else None
        except SportsProviderError as error:
            logger.warning("Sports provider data fetch failed: %s", error)
            return self._build_missing_payload(
                match_info,
                source_hint="sports-api-partial-failure",
                summary_lines=[
                    "Fixture найден, но часть вызовов sports API завершилась ошибкой, поэтому факты не были догенерированы моделью.",
                    "В ответе сохранены только безопасные fallback-значения до восстановления API-источника.",
                ],
            )

        home_standing_entry = next((row for row in standings_rows if (row.get("team") or {}).get("id") == home_team_id), None)
        away_standing_entry = next((row for row in standings_rows if (row.get("team") or {}).get("id") == away_team_id), None)
        home_standing = self._make_team_standing(home_standing_entry)
        away_standing = self._make_team_standing(away_standing_entry)
        lineup_payload = self._build_lineups(lineups, home_team_id, away_team_id)
        h2h_lines = self._build_h2h_lines(h2h)
        corners = self._build_corners(match_context, home_team.get("name") or "", away_team.get("name") or "")
        cards = self._build_cards(match_context, home_team.get("name") or "", away_team.get("name") or "", referee)

        blocks_presence = {
            "standings": bool(home_standing or away_standing),
            "lineups": bool((lineup_payload.get("home") or {}).get("players") or (lineup_payload.get("away") or {}).get("players")),
            "injuries": bool(home_injuries or away_injuries),
            "h2h": bool(h2h_lines),
            "form": bool(home_form or away_form),
            "stats": bool(home_stats or away_stats),
            "referee": bool(referee),
            "cards": bool(cards.get("trends")),
            "corners": bool(corners.get("trends")),
        }
        for block_name, has_data in blocks_presence.items():
            self._log_block_source(block_name, has_data)

        payload = {
            "sport": "football",
            "league": league.get("name") or match_info.get("league") or "Турнир уточняется",
            "match_time": self.sports_provider._extract_datetime(fixture_data.get("date")),
            "home_team": home_team.get("name") or match_info.get("home_team") or "Хозяева",
            "away_team": away_team.get("name") or match_info.get("away_team") or "Гости",
            "home_away_label": "домашний матч",
            "source_hint": self.sports_provider.provider_name,
            "standings": {
                "home": home_standing,
                "away": away_standing,
            },
            "motivation": self._build_motivation(home_standing, away_standing),
            "lineups": lineup_payload,
            "absences": {
                "home": self._build_absence_lines(home_injuries),
                "away": self._build_absence_lines(away_injuries),
            },
            "lineup_impact_lines": [
                "Составы формируются только из lineups API.",
                "При отсутствии подтверждения используется безопасный fallback без генерации фактов.",
            ],
            "lineup_impact_summary": "Оценка влияния составов ограничена подтверждёнными данными по стартовым XI и травмам.",
            "form": {
                "home": self._build_form_block(home_form, home_team_id),
                "away": self._build_form_block(away_form, away_team_id),
            },
            "h2h_lines": h2h_lines,
            "h2h_summary": "Вывод по личным встречам сформирован только по API-данным." if h2h_lines else "Недостаточно данных",
            "home_away": self._build_home_away_lines(home_stats, away_stats),
            "goal_trends": self._build_goal_trends(home_stats, away_stats),
            "corners": corners,
            "cards": cards,
            "key_numbers": self._build_key_numbers(home_stats, away_stats),
            "summary_lines": self._build_summary_lines(match_info, home_standing, away_standing, h2h_lines),
            "confidence_percent": "78" if standings_rows else "62",
            "time_to_start": self._build_time_to_start(fixture_data.get("date")),
        }
        return MatchAnalysisData.from_dict(payload)
