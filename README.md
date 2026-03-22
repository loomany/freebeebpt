# FreeBeeBPT Bot

FreeBeeBPT is a Telegram bot built with aiogram, OpenAI, and a real football data provider. OpenAI is used only to recognize a match from a screenshot/text and optionally normalize names, while factual Match Center blocks are filled from sports API data.

## Installation

1. Clone the repository and (optionally) create a virtual environment.
2. Install the dependencies:
   ```bash
   pip install -r requirements.txt
   ```
3. Create a `.env` file based on `.env.example` and fill in the required variables.

### Environment variables

- `BOT_TOKEN` – Telegram bot token.
- `OPENAI_API_KEY` – your OpenAI API key for screenshot/text match recognition only.
- `SPORTS_API_KEY` – API-Football / API-Sports key for real match data.
- `SPORTS_API_HOST` – API host, defaults to `v3.football.api-sports.io`.
- `SPORTS_API_PROVIDER` – provider label used in logs/output, defaults to `api-football`.
- `SPORTS_API_BASE_URL` – provider base URL, defaults to `https://v3.football.api-sports.io`.
- `ADMIN_ID` – optional Telegram ID for registration notifications; if omitted, the bot skips admin alerts.
- `LOG_LEVEL` – Python logging level, defaults to `INFO`.

## Usage

### Local run

```bash
python bot.py
```

### Debugging API-Football

- Use the Telegram command `/test_api` to call `/status` and `/fixtures?team=33&next=10`.
- Runtime logs now include `[API REQUEST]`, `[API RESPONSE STATUS]`, `[API RESPONSE BODY]`, `[FIND FIXTURE]`, `[API] team1=... → id=...`, `[API] team2=... → id=...`, `[API] from=YYYY-MM-DD, [API] to=YYYY-MM-DD`, `[API] fixtures count=...`, `[API] found match=YES/NO`, and `[DATA SOURCE] ... = OK/EMPTY`.

### Docker

```bash
docker build -t freebeebpt .
docker run --env-file .env freebeebpt
```


## Data source architecture

- `ALLOW_LLM_FOR_FACTS = False` in `services/match_data_service.py` explicitly forbids LLM-generated factual blocks.
- `services/sports_provider.py` integrates API-Football-compatible endpoints for fixtures, standings, lineups, injuries, H2H, form, team stats, match context, and referee.
- If an API block is unavailable, Match Center uses deterministic fallbacks such as `Составы уточняются`, `Данные по судье уточняются`, `Недостаточно данных`, and `Существенных потерь не выявлено`.
- Runtime logs report factual block coverage, for example: `[DATA SOURCE] standings = OK` or `[DATA SOURCE] cards = EMPTY`.
