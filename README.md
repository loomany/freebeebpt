# FreeBeeBPT Bot

FreeBeeBPT is a Telegram bot built with aiogram and OpenAI. It provides structured football Match Center analysis when users send match text or screenshots, and offers a cashback top-up CTA via inline buttons.

## Installation

1. Clone the repository and (optionally) create a virtual environment.
2. Install the dependencies:
   ```bash
   pip install -r requirements.txt
   ```
3. Create a `.env` file based on `.env.example` and fill in the required variables.

### Environment variables

- `BOT_TOKEN` – Telegram bot token.
- `OPENAI_API_KEY` – your OpenAI API key.
- `ADMIN_ID` – optional Telegram ID for registration notifications; if omitted, the bot skips admin alerts.

## Usage

### Local run

```bash
python bot.py
```

### Docker

```bash
docker build -t freebeebpt .
docker run --env-file .env freebeebpt
```
