# FreeBeeBPT News Bot

Telegram-бот получает спортивные новости из GNews, извлекает полный текст статьи, прогоняет материал через OpenAI-фильтр и отправляет в Telegram только короткую важную выжимку на казахском языке.

## Пайплайн

1. Планировщик запускает цикл по `NEWS_POLL_MINUTES`.
2. При `NEWS_TOPIC_ROTATION=true` за цикл проверяется только одна тема по кругу (`football`, `tennis`, `hockey`, `basketball`).
3. Бот получает до `GNEWS_MAX_RESULTS` новостей из GNews.
4. Перед AI-обработкой бот проверяет антидубль по `url`, а если URL нет — по `source_name + normalized_title`.
5. Для новой статьи бот пытается извлечь полный текст через `trafilatura`, затем через `readability-lxml + BeautifulSoup`, затем через простой HTML parse.
6. Если полный текст короткий или недоступен, используется fallback из `content`, либо `title + description`.
7. OpenAI возвращает строго JSON с фильтром важности, казахским summary, ключевыми тезисами и optional impact-блоками.
8. Бот отправляет только новости с `importance_score >= NEWS_IMPORTANCE_MIN_SCORE` и `importance_level in {high, top}`.

## Новые переменные окружения

- `GNEWS_API_KEY`
- `GNEWS_BASE_URL=https://gnews.io/api/v4`
- `GNEWS_LANGUAGE=en`
- `GNEWS_MAX_RESULTS=5`
- `GNEWS_DAILY_LIMIT=96`
- `NEWS_ENABLED=true`
- `NEWS_POLL_MINUTES=15`
- `NEWS_TOPIC_ROTATION=true`
- `NEWS_TOPICS=football,tennis,hockey,basketball`
- `ARTICLE_EXTRACT_ENABLED=true`
- `ARTICLE_EXTRACT_TIMEOUT=15`
- `ARTICLE_MIN_TEXT_LENGTH=800`
- `SEND_PHOTO_ENABLED=true`
- `OPENAI_API_KEY`
- `OPENAI_ENABLED=true`
- `OPENAI_MODEL=gpt-5.4`
- `NEWS_IMPORTANCE_MIN_SCORE=75`
- `BOT_TOKEN`
- `ADMIN_ID`
- `TELEGRAM_NEWS_CHAT_ID`
- `NEWS_POST_MODE=admin|channel`
- `LOG_LEVEL`

## Админ-команды

- `/news_status`
- `/fetch_news_now`
- `/fetch_topic football|tennis|hockey|basketball`
- `/news_test`
- `/news_test_ai`
- `/news_test_raw`
- `/news_test_compare`

## Основные модули

- `services/ai_news_processor.py` — OpenAI integration, retry, JSON validation через Pydantic.
- `services/news_ranker.py` — порог важности и решение по отправке.
- `services/telegram_formatter.py` — короткий Telegram-формат без сырого текста статьи.
- `prompts/news_prompt.py` — system/user prompts для JSON-ответа.
- `services/news_pipeline.py` — orchestration пайплайна и debug-команды.

## Локальный запуск

```bash
pip install -r requirements.txt
python bot.py
```
