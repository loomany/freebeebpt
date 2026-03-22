from __future__ import annotations

import asyncio
import json
import logging
import os
import urllib.parse
import urllib.request
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.error import HTTPError, URLError

logger = logging.getLogger(__name__)

API_FOOTBALL_BASE_URL = os.getenv("SPORTS_API_BASE_URL", "https://v3.football.api-sports.io")
SPORTS_API_KEY = os.getenv("SPORTS_API_KEY")
SPORTS_API_HOST = os.getenv("SPORTS_API_HOST", "v3.football.api-sports.io")
SPORTS_API_PROVIDER = os.getenv("SPORTS_API_PROVIDER", "api-football")

if SPORTS_API_KEY is None or not SPORTS_API_KEY.strip():
    logger.error("SPORTS_API_KEY is missing or empty")


class SportsProviderError(RuntimeError):
    pass


class SportsProvider:
    def __init__(self, api_key: str | None = None, base_url: str = API_FOOTBALL_BASE_URL, provider_name: str = SPORTS_API_PROVIDER):
        env_api_key = api_key if api_key is not None else os.getenv("SPORTS_API_KEY")
        self.api_key = env_api_key.strip() if isinstance(env_api_key, str) else None
        self.base_url = base_url.rstrip("/")
        self.provider_name = provider_name
        self.last_api_call_attempted = False

        if self.api_key is None or not self.api_key:
            logger.error("SPORTS_API_KEY is missing or empty")

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    def _build_headers(self) -> dict[str, str]:
        return {
            "x-apisports-key": self.api_key or "",
        }

    def _truncate_body_for_log(self, body: str) -> str:
        return body[:300]

    async def _get(self, path: str, **params: Any) -> dict[str, Any]:
        if not self.api_key:
            logger.error("SPORTS_API_KEY is missing or empty")
            raise SportsProviderError("SPORTS_API_KEY is not configured")

        query = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None and v != ""})
        url = f"{self.base_url}{path}"
        if query:
            url = f"{url}?{query}"

        logger.info("[API REQUEST] %s params=%s", path, params)
        self.last_api_call_attempted = True

        def _request() -> dict[str, Any]:
            request = urllib.request.Request(url, headers=self._build_headers())
            try:
                with urllib.request.urlopen(request, timeout=20) as response:
                    status_code = getattr(response, "status", response.getcode())
                    body = response.read().decode("utf-8")
                    logger.info("[API RESPONSE STATUS] %s", status_code)
                    logger.info("[API RESPONSE BODY] %s", self._truncate_body_for_log(body))
                    return json.loads(body)
            except HTTPError as error:
                error_body = error.read().decode("utf-8", errors="replace")
                logger.error("[API RESPONSE STATUS] %s", error.code)
                logger.error("[API RESPONSE BODY] %s", self._truncate_body_for_log(error_body))
                raise SportsProviderError(f"Request failed for {path}: HTTP {error.code} - {error_body}") from error
            except URLError as error:
                logger.error("[API RESPONSE STATUS] network-error")
                logger.error("[API RESPONSE BODY] %s", error)
                raise SportsProviderError(f"Request failed for {path}: {error}") from error
            except Exception as error:  # noqa: BLE001
                logger.error("[API RESPONSE STATUS] unexpected-error")
                logger.error("[API RESPONSE BODY] %s", error)
                raise SportsProviderError(f"Request failed for {path}: {error}") from error

        return await asyncio.to_thread(_request)

    async def check_api_status(self) -> dict[str, Any]:
        payload = await self._get("/status")
        return payload

    @staticmethod
    def _normalize_name(name: str | None) -> str:
        return "".join(ch.lower() for ch in (name or "") if ch.isalnum())

    @staticmethod
    def _extract_date(raw_date: str | None) -> str | None:
        if not raw_date:
            return None
        normalized = raw_date.replace("Z", "+00:00")
        try:
            return datetime.fromisoformat(normalized).date().isoformat()
        except ValueError:
            return raw_date[:10] if len(raw_date) >= 10 else None

    @staticmethod
    def _extract_datetime(raw_date: str | None) -> str:
        if not raw_date:
            return "Время уточняется"
        normalized = raw_date.replace("Z", "+00:00")
        try:
            return datetime.fromisoformat(normalized).astimezone(UTC).strftime("%Y-%m-%d %H:%M UTC")
        except ValueError:
            return raw_date

    @staticmethod
    def _safe_avg(value: float | int | None) -> str:
        if value is None:
            return "нет данных"
        return f"{float(value):.2f}".rstrip("0").rstrip(".")

    async def search_team(self, team_name: str) -> dict[str, Any] | None:
        payload = await self._get("/teams", search=team_name)
        response = payload.get("response") or []
        return response[0] if response else None

    async def find_fixture(self, home_team: str, away_team: str, date: str | None) -> dict[str, Any] | None:
        logger.info("[FIND FIXTURE] home_team=%s away_team=%s match_date=%s", home_team, away_team, date)
        home_team_data = await self.search_team(home_team)
        away_team_data = await self.search_team(away_team)
        if not home_team_data or not away_team_data:
            logger.warning("[API WARNING] fixture not found")
            return None

        home_team_id = home_team_data["team"]["id"]
        away_team_id = away_team_data["team"]["id"]
        match_date = self._extract_date(date)
        candidate_dates = [match_date] if match_date else []
        if not candidate_dates:
            today = datetime.now(UTC).date()
            candidate_dates = [(today + timedelta(days=delta)).isoformat() for delta in range(-2, 8)]

        for candidate_date in candidate_dates:
            payload = await self._get("/fixtures", team=home_team_id, date=candidate_date, season=candidate_date[:4])
            fixtures = payload.get("response") or []
            for fixture in fixtures:
                teams = fixture.get("teams") or {}
                home = teams.get("home") or {}
                away = teams.get("away") or {}
                if home.get("id") == home_team_id and away.get("id") == away_team_id:
                    return fixture
                normalized_home = self._normalize_name(home.get("name"))
                normalized_away = self._normalize_name(away.get("name"))
                if normalized_home == self._normalize_name(home_team) and normalized_away == self._normalize_name(away_team):
                    return fixture
        logger.warning("[API WARNING] fixture not found")
        return None

    async def get_standings(self, league_id: int, season: int) -> list[dict[str, Any]]:
        payload = await self._get("/standings", league=league_id, season=season)
        response = payload.get("response") or []
        if not response:
            return []
        league = response[0].get("league") or {}
        standings_groups = league.get("standings") or []
        return standings_groups[0] if standings_groups else []

    async def get_lineups(self, fixture_id: int) -> list[dict[str, Any]]:
        payload = await self._get("/fixtures/lineups", fixture=fixture_id)
        return payload.get("response") or []

    async def get_injuries(self, team_id: int, league_id: int, season: int) -> list[dict[str, Any]]:
        payload = await self._get("/injuries", team=team_id, league=league_id, season=season)
        return payload.get("response") or []

    async def get_h2h(self, home_team_id: int, away_team_id: int) -> list[dict[str, Any]]:
        payload = await self._get("/fixtures/headtohead", h2h=f"{home_team_id}-{away_team_id}", last=5)
        return payload.get("response") or []

    async def get_team_form(self, team_id: int, league_id: int, season: int) -> list[dict[str, Any]]:
        payload = await self._get("/fixtures", team=team_id, league=league_id, season=season, last=5, status="FT")
        return payload.get("response") or []

    async def get_team_stats(self, team_id: int, league_id: int, season: int) -> dict[str, Any]:
        payload = await self._get("/teams/statistics", team=team_id, league=league_id, season=season)
        return payload.get("response") or {}

    async def get_match_context(self, fixture_id: int) -> dict[str, Any]:
        payload = await self._get("/fixtures", id=fixture_id)
        response = payload.get("response") or []
        return response[0] if response else {}

    async def get_referee(self, fixture_id: int) -> str | None:
        context = await self.get_match_context(fixture_id)
        fixture = context.get("fixture") or {}
        return fixture.get("referee")

    async def debug_last_fixture(self, team_id: int = 33) -> dict[str, Any]:
        payload = await self._get("/fixtures", team=team_id, last=1)
        logger.info("[TEST API RAW JSON] %s", json.dumps(payload, ensure_ascii=False)[:3000])
        return payload
