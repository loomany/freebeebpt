# FreeBeeBPT Bot

FreeBeeBPT is a Telegram bot built with aiogram and OpenAI. It provides sports predictions when users send match text or screenshots.

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
- `ADMIN_ID` – Telegram ID for registration notifications.

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
