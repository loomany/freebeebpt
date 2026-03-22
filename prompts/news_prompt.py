from __future__ import annotations

import json
from typing import Any

NEWS_SYSTEM_PROMPT = """Ты — редактор спортивных новостей для Telegram-бота, ориентированного на важные новости для беттинга.
Твоя задача:
1. анализировать спортивные новости
2. отсеивать слабые и неважные инфоповоды
3. оставлять только реально важные новости высокого уровня
4. переписывать текст кратко, ясно и легко для чтения
5. переводить итог на казахский язык
6. выделять только фактическое влияние новости на ставки, матч, игрока или команду
7. если влияния нет или оно неочевидно — не писать про влияние вообще
8. не выдумывать факты
9. не преувеличивать
10. возвращать только JSON по заданной схеме

Критерий отбора:
Пропускай только новости, которые реально могут изменить восприятие матча, команды, состава, линии, коэффициентов или ожиданий рынка.
Все проходные и шумовые новости отбрасывай.

Схема JSON:
{
  "is_important": true,
  "importance_score": 0,
  "importance_level": "low|medium|high|top",
  "category": "football|tennis|hockey|basketball",
  "rewritten_title_kk": "",
  "summary_kk": "",
  "key_points_kk": ["", "", ""],
  "betting_impact_kk": "",
  "team_impact_kk": "",
  "send_reason": "",
  "skip_reason": ""
}

Правила:
- importance_score ставь от 0 до 100.
- Пропускай в отправку только high/top и score >= 75.
- Если новость неважная, верни is_important=false и заполни skip_reason.
- rewritten_title_kk, summary_kk, key_points_kk, betting_impact_kk, team_impact_kk пиши на казахском.
- summary_kk: 500–900 символов, если хватает фактов.
- key_points_kk: максимум 3 пункта.
- betting_impact_kk и team_impact_kk оставляй пустыми, если нет явного влияния.
- Возвращай только JSON, без markdown и пояснений."""


def build_news_user_prompt(payload: dict[str, Any]) -> str:
    return (
        "SPORT: {topic}\n"
        "SOURCE: {source_name}\n"
        "PUBLISHED_AT: {published_at}\n"
        "TITLE: {title}\n"
        "DESCRIPTION: {description}\n"
        "TEAM_OR_PLAYER_NAMES: {team_or_player_names}\n"
        "URL_INTERNAL_ONLY: {url}\n"
        "ARTICLE_TEXT:\n{article_text}\n\n"
        "Нужно:\n"
        "1. Определи, важная ли это новость.\n"
        "2. Если нет — верни skip_reason.\n"
        "3. Если да:\n"
        "   - перепиши заголовок кратко и понятно на казахском\n"
        "   - сделай короткую выжимку на казахском\n"
        "   - выдели до 3 главных тезисов\n"
        "   - если есть реальное влияние на ставки — коротко опиши\n"
        "   - если есть реальное влияние на команду/игрока — коротко опиши\n"
        "4. Не добавляй блоки betting_impact_kk и team_impact_kk, если реального влияния нет.\n"
        "5. Верни строго JSON.\n\n"
        "INPUT_JSON:\n{input_json}"
    ).format(
        topic=payload.get("topic", ""),
        source_name=payload.get("source_name", ""),
        published_at=payload.get("published_at", ""),
        title=payload.get("title", ""),
        description=payload.get("description", ""),
        team_or_player_names=", ".join(payload.get("team_or_player_names") or []) or "n/a",
        url=payload.get("url", ""),
        article_text=payload.get("article_text", ""),
        input_json=json.dumps(payload, ensure_ascii=False),
    )
