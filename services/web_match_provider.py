from __future__ import annotations

import asyncio
import json
import logging
import re
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import UTC, datetime
from html import unescape
from typing import Any

from openai import AsyncOpenAI

logger = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
ALLOWED_SOURCE_PATTERNS = (
    "espn.",
    "sofascore.",
    "flashscore.",
    "bbc.",
    "skysports.",
    "eurosport.",
    "livescore.",
    "premierleague.",
    "laliga.",
    "bundesliga.",
    "seriea.",
    "ligue1.",
    "uefa.",
    "fifa.",
    "nba.",
    "nhl.",
    "mlb.",
    "atptour.",
    "wtatennis.",
)
SEARCH_ENDPOINT = "https://html.duckduckgo.com/html/"
MAX_SEARCH_RESULTS = 8
MAX_PAGE_CHARS = 6000
ALLOW_LLM_FOR_FACTS = False


class WebMatchProviderError(RuntimeError):
    pass


@dataclass
class WebSource:
    url: str
    domain: str
    title: str = ""
    snippet: str = ""
    content: str = ""


@dataclass
class WebMatchContext:
    match_query: dict[str, Any]
    sources: list[WebSource] = field(default_factory=list)
    facts: dict[str, Any] = field(default_factory=dict)


class WebMatchProvider:
    def __init__(self, client: AsyncOpenAI):
        self.client = client

    @staticmethod
    def _normalize_url(raw_url: str) -> str | None:
        if not raw_url:
            return None
        decoded = unescape(raw_url)
        if decoded.startswith("//"):
            decoded = f"https:{decoded}"
        if not decoded.startswith(("http://", "https://")):
            return None
        parsed = urllib.parse.urlparse(decoded)
        if not parsed.netloc:
            return None
        return decoded

    @staticmethod
    def _domain_allowed(url: str) -> bool:
        domain = urllib.parse.urlparse(url).netloc.lower()
        return any(pattern in domain for pattern in ALLOWED_SOURCE_PATTERNS)

    @staticmethod
    def _strip_html(html: str) -> str:
        text = re.sub(r"<script.*?</script>", " ", html, flags=re.S | re.I)
        text = re.sub(r"<style.*?</style>", " ", text, flags=re.S | re.I)
        text = re.sub(r"<[^>]+>", " ", text)
        text = unescape(text)
        text = re.sub(r"\s+", " ", text)
        return text.strip()

    async def _fetch_text(self, url: str) -> str:
        def _request() -> str:
            request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(request, timeout=20) as response:
                charset = response.headers.get_content_charset() or "utf-8"
                raw = response.read().decode(charset, errors="ignore")
                return raw

        return await asyncio.to_thread(_request)

    async def search_match_pages(self, match_info: dict[str, Any]) -> list[WebSource]:
        queries = self._build_search_queries(match_info)
        gathered: list[WebSource] = []
        seen_urls: set[str] = set()

        for query in queries:
            html = await self._fetch_text(f"{SEARCH_ENDPOINT}?{urllib.parse.urlencode({'q': query})}")
            for match in re.finditer(r'<a[^>]+class="result__a"[^>]+href="(?P<href>[^"]+)"[^>]*>(?P<title>.*?)</a>', html, flags=re.I | re.S):
                url = self._normalize_url(match.group("href"))
                if not url or url in seen_urls or not self._domain_allowed(url):
                    continue
                title = self._strip_html(match.group("title"))
                domain = urllib.parse.urlparse(url).netloc.lower()
                gathered.append(WebSource(url=url, domain=domain, title=title))
                seen_urls.add(url)
                if len(gathered) >= MAX_SEARCH_RESULTS:
                    return gathered
        return gathered

    def _build_search_queries(self, match_info: dict[str, Any]) -> list[str]:
        home = match_info.get("home_team") or ""
        away = match_info.get("away_team") or ""
        league = match_info.get("league") or ""
        dt = str(match_info.get("match_datetime") or "")[:10]
        base = f'{home} vs {away} {league} {dt}'.strip()
        return [
            f"{base} preview lineups injuries standings referee",
            f"{base} sofascore espn",
            f"{home} {away} h2h form flashscore",
        ]

    async def find_match_sources(self, match_info: dict[str, Any]) -> WebMatchContext:
        sources = await self.search_match_pages(match_info)
        enriched: list[WebSource] = []
        for source in sources:
            try:
                html = await self._fetch_text(source.url)
            except Exception as error:  # noqa: BLE001
                logger.warning("[WEB SOURCE] fetch_failed domain=%s url=%s error=%s", source.domain, source.url, error)
                continue
            source.content = self._strip_html(html)[:MAX_PAGE_CHARS]
            enriched.append(source)
            logger.info("[WEB SOURCE] source_url=%s source_domain=%s", source.url, source.domain)
        return WebMatchContext(match_query=match_info, sources=enriched)

    async def extract_match_facts_from_pages(self, context: WebMatchContext) -> dict[str, Any]:
        if ALLOW_LLM_FOR_FACTS:
            raise RuntimeError("ALLOW_LLM_FOR_FACTS must remain False for Match Center factual data")
        if not context.sources:
            return {}

        source_payload = [
            {
                "url": item.url,
                "domain": item.domain,
                "title": item.title,
                "content": item.content,
            }
            for item in context.sources[:5]
        ]
        response = await self.client.chat.completions.create(
            model="gpt-4o",
            temperature=0,
            max_tokens=2200,
            response_format={"type": "json_object"},
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Ты извлекаешь факты о матче только из переданных текстов веб-страниц. "
                        "Не придумывай и не достраивай пропуски. "
                        "Верни JSON с полями: league, match_time, source_domains, standings, motivation, lineups, absences, lineup_impact_lines, lineup_impact_summary, form, h2h_lines, h2h_summary, home_away, goal_trends, corners, cards, key_numbers, summary_lines. "
                        "Для каждого отсутствующего блока используй безопасные fallback-значения: 'Данные уточняются' или 'Недостаточно данных'. "
                        "standings.home/away должны содержать position, played, wins, draws, losses, points, goals_for, goals_against. "
                        "lineups.home/away должны содержать formation и players. "
                        "cards.referee_line заполняй только если судья явно найден в тексте. "
                        "Не добавляй букмекерские советы и коэффициенты."
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "match_info": context.match_query,
                            "sources": source_payload,
                        },
                        ensure_ascii=False,
                    ),
                },
            ],
        )
        content = response.choices[0].message.content or "{}"
        return json.loads(content)

    @staticmethod
    def _safe_standing(data: dict[str, Any] | None) -> dict[str, str]:
        fallback = "Данные по таблице уточняются"
        base = data or {}
        return {
            "position": str(base.get("position") or fallback),
            "played": str(base.get("played") or fallback),
            "wins": str(base.get("wins") or fallback),
            "draws": str(base.get("draws") or fallback),
            "losses": str(base.get("losses") or fallback),
            "points": str(base.get("points") or fallback),
            "goals_for": str(base.get("goals_for") or fallback),
            "goals_against": str(base.get("goals_against") or fallback),
        }

    @staticmethod
    def _normalize_form_team(data: dict[str, Any] | None) -> dict[str, str]:
        base = data or {}
        return {
            "icons": str(base.get("icons") or "—"),
            "goals_for": str(base.get("goals_for") or "Данные уточняются"),
            "goals_against": str(base.get("goals_against") or "Данные уточняются"),
        }

    @staticmethod
    def _normalize_lineup(data: dict[str, Any] | None) -> dict[str, Any]:
        base = data or {}
        return {
            "formation": str(base.get("formation") or "Составы уточняются"),
            "players": [str(item) for item in (base.get("players") or []) if str(item).strip()],
        }

    @staticmethod
    def _normalize_list(values: Any, fallback: str | None = None) -> list[str]:
        lines = [str(item).strip() for item in (values or []) if str(item).strip()]
        if not lines and fallback:
            lines = [fallback]
        return lines

    @staticmethod
    def _build_time_to_start(match_time: str | None) -> str:
        if not match_time:
            return "уточняется"
        for candidate in (match_time, match_time.replace(" UTC", "+00:00")):
            try:
                dt = datetime.fromisoformat(candidate.replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=UTC)
                delta = dt - datetime.now(UTC)
                hours = int(delta.total_seconds() // 3600)
                if hours < 0:
                    return "матч уже начался или завершился"
                days, rem_hours = divmod(hours, 24)
                return f"{days}д {rem_hours}ч"
            except ValueError:
                continue
        return "уточняется"

    def _log_block_status(self, payload: dict[str, Any]) -> None:
        mapping = {
            "standings": bool((payload.get("standings") or {}).get("home") or (payload.get("standings") or {}).get("away")),
            "lineups": bool(((payload.get("lineups") or {}).get("home") or {}).get("players") or ((payload.get("lineups") or {}).get("away") or {}).get("players")),
            "injuries": bool((payload.get("absences") or {}).get("home") or (payload.get("absences") or {}).get("away")),
            "h2h": bool(payload.get("h2h_lines")),
            "form": bool((payload.get("form") or {}).get("home") or (payload.get("form") or {}).get("away")),
            "cards": any("Данные" not in value for value in [((payload.get("cards") or {}).get("home_stat") or ""), ((payload.get("cards") or {}).get("away_stat") or "")]) or bool((payload.get("cards") or {}).get("trends")),
            "corners": any("Данные" not in value for value in [((payload.get("corners") or {}).get("home_stat") or ""), ((payload.get("corners") or {}).get("away_stat") or "")]) or bool((payload.get("corners") or {}).get("trends")),
            "referee": "уточняются" not in str((payload.get("cards") or {}).get("referee_line") or "").lower(),
        }
        for name, has_data in mapping.items():
            logger.info("[WEB SOURCE] %s: %s", name, "FOUND" if has_data else "MISSING")

    async def build_match_analysis_data(self, match_info: dict[str, Any]) -> dict[str, Any] | None:
        context = await self.find_match_sources(match_info)
        if not context.sources:
            return None
        facts = await self.extract_match_facts_from_pages(context)
        standings = facts.get("standings") or {}
        payload = {
            "sport": match_info.get("sport") or "football",
            "league": facts.get("league") or match_info.get("league") or "Турнир уточняется",
            "match_time": facts.get("match_time") or match_info.get("match_datetime") or "Время уточняется",
            "home_team": match_info.get("home_team") or "Хозяева",
            "away_team": match_info.get("away_team") or "Гости",
            "home_away_label": "домашний матч",
            "source_hint": "web-search",
            "standings": {
                "home": self._safe_standing((standings.get("home") if isinstance(standings, dict) else None)),
                "away": self._safe_standing((standings.get("away") if isinstance(standings, dict) else None)),
            },
            "motivation": {
                "home_lines": self._normalize_list((facts.get("motivation") or {}).get("home_lines")),
                "away_lines": self._normalize_list((facts.get("motivation") or {}).get("away_lines")),
                "home_summary": (facts.get("motivation") or {}).get("home_summary") or "Данные уточняются",
                "away_summary": (facts.get("motivation") or {}).get("away_summary") or "Данные уточняются",
            },
            "lineups": {
                "home": self._normalize_lineup((facts.get("lineups") or {}).get("home")),
                "away": self._normalize_lineup((facts.get("lineups") or {}).get("away")),
            },
            "absences": {
                "home": self._normalize_list((facts.get("absences") or {}).get("home")),
                "away": self._normalize_list((facts.get("absences") or {}).get("away")),
            },
            "lineup_impact_lines": self._normalize_list(facts.get("lineup_impact_lines")),
            "lineup_impact_summary": facts.get("lineup_impact_summary") or "Составы уточняются, влияние на игру оценивается предварительно",
            "form": {
                "home": self._normalize_form_team((facts.get("form") or {}).get("home")),
                "away": self._normalize_form_team((facts.get("form") or {}).get("away")),
            },
            "h2h_lines": self._normalize_list(facts.get("h2h_lines")),
            "h2h_summary": facts.get("h2h_summary") or "Недостаточно данных",
            "home_away": {
                "home_lines": self._normalize_list((facts.get("home_away") or {}).get("home_lines")),
                "away_lines": self._normalize_list((facts.get("home_away") or {}).get("away_lines")),
            },
            "goal_trends": self._normalize_list(facts.get("goal_trends")),
            "corners": {
                "home_stat": (facts.get("corners") or {}).get("home_stat") or "Недостаточно данных",
                "away_stat": (facts.get("corners") or {}).get("away_stat") or "Недостаточно данных",
                "trends": self._normalize_list((facts.get("corners") or {}).get("trends")),
            },
            "cards": {
                "home_stat": (facts.get("cards") or {}).get("home_stat") or "Недостаточно данных",
                "away_stat": (facts.get("cards") or {}).get("away_stat") or "Недостаточно данных",
                "referee_line": (facts.get("cards") or {}).get("referee_line") or "Данные по судье уточняются",
                "trends": self._normalize_list((facts.get("cards") or {}).get("trends")),
            },
            "key_numbers": {
                "home_avg_goals": str((facts.get("key_numbers") or {}).get("home_avg_goals") or "Данные уточняются"),
                "away_avg_goals": str((facts.get("key_numbers") or {}).get("away_avg_goals") or "Данные уточняются"),
                "home_avg_conceded": str((facts.get("key_numbers") or {}).get("home_avg_conceded") or "Данные уточняются"),
                "away_avg_conceded": str((facts.get("key_numbers") or {}).get("away_avg_conceded") or "Данные уточняются"),
            },
            "summary_lines": self._normalize_list(facts.get("summary_lines"), "Матч оценивается только по найденным в вебе подтверждённым данным."),
            "confidence_percent": str(match_info.get("confidence") or facts.get("confidence_percent") or "70"),
            "time_to_start": self._build_time_to_start(facts.get("match_time") or match_info.get("match_datetime")),
        }
        self._log_block_status(payload)
        return payload
