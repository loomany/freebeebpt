from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class TeamStanding:
    position: str = "Данные по таблице уточняются"
    played: str = "Данные по таблице уточняются"
    wins: str = "Данные по таблице уточняются"
    draws: str = "Данные по таблице уточняются"
    losses: str = "Данные по таблице уточняются"
    points: str = "Данные по таблице уточняются"
    goals_for: str = "Данные по таблице уточняются"
    goals_against: str = "Данные по таблице уточняются"


@dataclass
class Standings:
    home: TeamStanding = field(default_factory=TeamStanding)
    away: TeamStanding = field(default_factory=TeamStanding)


@dataclass
class MotivationBlock:
    home_lines: list[str] = field(default_factory=list)
    away_lines: list[str] = field(default_factory=list)
    home_summary: str = "Данные уточняются"
    away_summary: str = "Данные уточняются"


@dataclass
class TeamLineup:
    formation: str = "Схема уточняется"
    players: list[str] = field(default_factory=list)


@dataclass
class Lineups:
    home: TeamLineup = field(default_factory=TeamLineup)
    away: TeamLineup = field(default_factory=TeamLineup)


@dataclass
class Absences:
    home: list[str] = field(default_factory=list)
    away: list[str] = field(default_factory=list)


@dataclass
class TeamForm:
    icons: str = "—"
    goals_for: str = "Данные уточняются"
    goals_against: str = "Данные уточняются"


@dataclass
class FormBlock:
    home: TeamForm = field(default_factory=TeamForm)
    away: TeamForm = field(default_factory=TeamForm)


@dataclass
class HomeAwayBlock:
    home_lines: list[str] = field(default_factory=list)
    away_lines: list[str] = field(default_factory=list)


@dataclass
class CornersBlock:
    home_stat: str = "Данные уточняются"
    away_stat: str = "Данные уточняются"
    trends: list[str] = field(default_factory=list)


@dataclass
class CardsBlock:
    home_stat: str = "Данные уточняются"
    away_stat: str = "Данные уточняются"
    referee_line: str = "Данные по судье уточняются"
    trends: list[str] = field(default_factory=list)


@dataclass
class KeyNumbers:
    home_avg_goals: str = "Данные уточняются"
    away_avg_goals: str = "Данные уточняются"
    home_avg_conceded: str = "Данные уточняются"
    away_avg_conceded: str = "Данные уточняются"


@dataclass
class MatchAnalysisData:
    sport: str = "football"
    league: str = "Турнир уточняется"
    match_time: str = "Время уточняется"
    home_team: str = "Хозяева"
    away_team: str = "Гости"
    home_away_label: str = "домашний матч"
    source_hint: str = "openai"
    standings: Standings = field(default_factory=Standings)
    motivation: MotivationBlock = field(default_factory=MotivationBlock)
    lineups: Lineups = field(default_factory=Lineups)
    absences: Absences = field(default_factory=Absences)
    lineup_impact_lines: list[str] = field(default_factory=list)
    lineup_impact_summary: str = "Влияние составов уточняется"
    form: FormBlock = field(default_factory=FormBlock)
    h2h_lines: list[str] = field(default_factory=list)
    h2h_summary: str = "Недостаточно данных"
    home_away: HomeAwayBlock = field(default_factory=HomeAwayBlock)
    goal_trends: list[str] = field(default_factory=list)
    corners: CornersBlock = field(default_factory=CornersBlock)
    cards: CardsBlock = field(default_factory=CardsBlock)
    key_numbers: KeyNumbers = field(default_factory=KeyNumbers)
    summary_lines: list[str] = field(default_factory=list)
    confidence_percent: str = "65"
    time_to_start: str = "уточняется"

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "MatchAnalysisData":
        standings = payload.get("standings", {}) or {}
        motivation = payload.get("motivation", {}) or {}
        lineups = payload.get("lineups", {}) or {}
        absences = payload.get("absences", {}) or {}
        form = payload.get("form", {}) or {}
        home_away = payload.get("home_away", {}) or {}
        corners = payload.get("corners", {}) or {}
        cards = payload.get("cards", {}) or {}
        key_numbers = payload.get("key_numbers", {}) or {}

        return cls(
            sport=payload.get("sport", "football"),
            league=payload.get("league", "Турнир уточняется"),
            match_time=payload.get("match_time", "Время уточняется"),
            home_team=payload.get("home_team", "Хозяева"),
            away_team=payload.get("away_team", "Гости"),
            home_away_label=payload.get("home_away_label", "домашний матч"),
            source_hint=payload.get("source_hint", "openai"),
            standings=Standings(
                home=TeamStanding(**(standings.get("home", {}) or {})),
                away=TeamStanding(**(standings.get("away", {}) or {})),
            ),
            motivation=MotivationBlock(
                home_lines=motivation.get("home_lines", []) or [],
                away_lines=motivation.get("away_lines", []) or [],
                home_summary=motivation.get("home_summary", "Данные уточняются"),
                away_summary=motivation.get("away_summary", "Данные уточняются"),
            ),
            lineups=Lineups(
                home=TeamLineup(**(lineups.get("home", {}) or {})),
                away=TeamLineup(**(lineups.get("away", {}) or {})),
            ),
            absences=Absences(
                home=absences.get("home", []) or [],
                away=absences.get("away", []) or [],
            ),
            lineup_impact_lines=payload.get("lineup_impact_lines", []) or [],
            lineup_impact_summary=payload.get("lineup_impact_summary", "Влияние составов уточняется"),
            form=FormBlock(
                home=TeamForm(**(form.get("home", {}) or {})),
                away=TeamForm(**(form.get("away", {}) or {})),
            ),
            h2h_lines=payload.get("h2h_lines", []) or [],
            h2h_summary=payload.get("h2h_summary", "Недостаточно данных"),
            home_away=HomeAwayBlock(
                home_lines=home_away.get("home_lines", []) or [],
                away_lines=home_away.get("away_lines", []) or [],
            ),
            goal_trends=payload.get("goal_trends", []) or [],
            corners=CornersBlock(
                home_stat=corners.get("home_stat", "Данные уточняются"),
                away_stat=corners.get("away_stat", "Данные уточняются"),
                trends=corners.get("trends", []) or [],
            ),
            cards=CardsBlock(
                home_stat=cards.get("home_stat", "Данные уточняются"),
                away_stat=cards.get("away_stat", "Данные уточняются"),
                referee_line=cards.get("referee_line", "Данные по судье уточняются"),
                trends=cards.get("trends", []) or [],
            ),
            key_numbers=KeyNumbers(**key_numbers),
            summary_lines=payload.get("summary_lines", []) or [],
            confidence_percent=str(payload.get("confidence_percent", "65")),
            time_to_start=payload.get("time_to_start", "уточняется"),
        )
