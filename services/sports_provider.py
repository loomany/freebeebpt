from __future__ import annotations

import asyncio
import json
import logging
import os
import urllib.parse
import urllib.request
from datetime import UTC, datetime, timedelta
from typing import Any

logger = logging.getLogger(__name__)

API_FOOTBALL_BASE_URL = os.getenv("SPORTS_API_BASE_URL", "https://v3.football.api-sports.io")
SPORTS_API_KEY = os.getenv("SPORTS_API_KEY")
SPORTS_API_HOST = os.getenv("SPORTS_API_HOST", "v3.football.api-sports.io")
SPORTS_API_PROVIDER = os.getenv("SPORTS_API_PROVIDER", "api-football")


class SportsProviderError(RuntimeError):
    pass


class SportsProvider:
    def __init__(self, api_key: str | None = None, base_url: str = API_FOOTBALL_BASE_URL, provider_name: str = SPORTS_API_PROVIDER):
        self.api_key = api_key or SPORTS_API_KEY
        self.base_url = base_url.rstrip("/")
        self.provider_name = provider_name

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    async def _get(self, path: str, **params: Any) -> dict[str, Any]:
        if not self.api_key:
            raise SportsProviderError("SPORTS_API_KEY is not configured")

        query = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None and v != ""})
        url = f"{self.base_url}{path}"
        if query:
            url = f"{url}?{query}"

        def _request() -> dict[str, Any]:
            request = urllib.request.Request(
                url,
                headers={
                    "x-apisports-key": self.api_key,
                    "x-rapidapi-key": self.api_key,
                    "x-rapidapi-host": SPORTS_API_HOST,
                },
            )
            try:
                with urllib.request.urlopen(request, timeout=20) as response:
                    return json.loads(response.read().decode("utf-8"))
            except Exception as error:  # noqa: BLE001
                raise SportsProviderError(f"Request failed for {path}: {error}") from error

        return await asyncio.to_thread(_request)

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
        home_team_data = await self.search_team(home_team)
        away_team_data = await self.search_team(away_team)
        if not home_team_data or not away_team_data:
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
