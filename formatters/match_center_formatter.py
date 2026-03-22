from __future__ import annotations

from models.match_analysis import MatchAnalysisData

SEPARATOR = "━━━━━━━━━━━━━━━"


def _bullet_lines(lines: list[str], fallback: str) -> str:
    prepared = [line.strip() for line in lines if line and line.strip()]
    if not prepared:
        prepared = [fallback]
    return "\n".join(f"— {line}" for line in prepared)


def _multiline_players(players: list[str]) -> str:
    prepared = [player.strip() for player in players if player and player.strip()]
    if not prepared:
        return "Составы уточняются"
    return "\n".join(prepared)


def _h2h_lines(lines: list[str]) -> str:
    prepared = [line.strip() for line in lines if line and line.strip()]
    if not prepared:
        prepared = ["Недостаточно данных", "Недостаточно данных", "Недостаточно данных"]
    while len(prepared) < 5:
        prepared.append("Недостаточно данных")
    return "\n".join(prepared[:5])


def build_match_center_text(data: MatchAnalysisData) -> str:
    return f"""⚽ {data.home_team} — {data.away_team}

🕒 {data.match_time}
🏟 {data.home_team} ({data.home_away_label})

{SEPARATOR}

🏆 Положение в таблице

{data.home_team}:
— {data.standings.home.position} место
— {data.standings.home.played} матчей
— {data.standings.home.wins} побед / {data.standings.home.draws} ничьих / {data.standings.home.losses} поражений
— {data.standings.home.points} очков
— голы: {data.standings.home.goals_for}-{data.standings.home.goals_against}

{data.away_team}:
— {data.standings.away.position} место
— {data.standings.away.played} матчей
— {data.standings.away.wins} побед / {data.standings.away.draws} ничьих / {data.standings.away.losses} поражений
— {data.standings.away.points} очков
— голы: {data.standings.away.goals_for}-{data.standings.away.goals_against}

{SEPARATOR}

📌 Турнирная мотивация

{data.home_team}:
{_bullet_lines(data.motivation.home_lines, 'Данные уточняются')}
👉 {data.motivation.home_summary}

{data.away_team}:
{_bullet_lines(data.motivation.away_lines, 'Данные уточняются')}
👉 {data.motivation.away_summary}

{SEPARATOR}

📋 Ожидаемые составы

{data.home_team} ({data.lineups.home.formation}):
{_multiline_players(data.lineups.home.players)}

{data.away_team} ({data.lineups.away.formation}):
{_multiline_players(data.lineups.away.players)}

{SEPARATOR}

🚑 Потери

{data.home_team}:
{_bullet_lines(data.absences.home, 'Существенных потерь не выявлено')}

{data.away_team}:
{_bullet_lines(data.absences.away, 'Существенных потерь не выявлено')}

{SEPARATOR}

📊 Влияние составов

{_bullet_lines(data.lineup_impact_lines, 'Составы уточняются, влияние на игру оценивается предварительно')}
👉 {data.lineup_impact_summary}

{SEPARATOR}

📈 Форма (5 матчей)

{data.home_team}: {data.form.home.icons}
— {data.form.home.goals_for} забито / {data.form.home.goals_against} пропущено

{data.away_team}: {data.form.away.icons}
— {data.form.away.goals_for} забито / {data.form.away.goals_against} пропущено

{SEPARATOR}

🤝 Личные встречи

{_h2h_lines(data.h2h_lines)}

👉 {data.h2h_summary}

{SEPARATOR}

🏟 Дом / Выезд

{data.home_team} дома:
{_bullet_lines(data.home_away.home_lines, 'Данные уточняются')}

{data.away_team} в гостях:
{_bullet_lines(data.home_away.away_lines, 'Данные уточняются')}

{SEPARATOR}

🔥 Голевые тренды

{_bullet_lines(data.goal_trends, 'Недостаточно данных по голевым трендам')}

{SEPARATOR}

🚩 Угловые

{data.home_team}:
— {data.corners.home_stat}

{data.away_team}:
— {data.corners.away_stat}

📊 Тренды:
{_bullet_lines(data.corners.trends, 'Данные по угловым уточняются')}

{SEPARATOR}

🟡 Жёлтые карточки

{data.home_team}:
— {data.cards.home_stat}

{data.away_team}:
— {data.cards.away_stat}

👨‍⚖️ Судья:
— {data.cards.referee_line}

📊 Тренды:
{_bullet_lines(data.cards.trends, 'Данные по карточкам уточняются')}

{SEPARATOR}

📊 Ключевые цифры

Голы:
{data.home_team} — {data.key_numbers.home_avg_goals}
{data.away_team} — {data.key_numbers.away_avg_goals}

Пропускают:
{data.home_team} — {data.key_numbers.home_avg_conceded}
{data.away_team} — {data.key_numbers.away_avg_conceded}

{SEPARATOR}

🧠 Вывод

""" + "\n".join(data.summary_lines[:2] or ["Матч оценивается на основе доступных данных, без букмекерских рекомендаций.", "Окончательная картина зависит от подтверждения составов и новостей ближе к старту."]) + f"""

{SEPARATOR}

📊 Уверенность: {data.confidence_percent}%

⏳ До начала: {data.time_to_start}"""
