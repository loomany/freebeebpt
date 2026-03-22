# FreeBeeBPT News Bot

Telegram-бот получает спортивные новости из GNews, не показывает ссылку пользователю, сам извлекает полный текст статьи, переводит итоговый материал на казахский и отправляет текст с картинкой в Telegram.

## Пайплайн

1. Планировщик запускает цикл по `NEWS_POLL_MINUTES`.
2. При `NEWS_TOPIC_ROTATION=true` за цикл проверяется только одна тема по кругу (`football`, `tennis`, `hockey`, `basketball`), чтобы стабильно держаться в лимите GNews.
3. Бот получает до `GNEWS_MAX_RESULTS` новостей из GNews.
4. Перед парсингом страницы бот проверяет антидубль по `url`, а если URL нет — по `source_name + normalized_title`.
5. Для новой статьи бот пытается извлечь полный текст через `trafilatura`, затем через `readability-lxml + BeautifulSoup`, затем через простой HTML parse.
6. Если полный текст короткий или недоступен, используется fallback из `content`, либо `title + description`.
7. Итоговый текст переводится на казахский через OpenAI, а при ошибке отправляется оригинал.
8. В Telegram уходит фото (если валидно) и текст без ссылки. Длинные статьи режутся на несколько сообщений.

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
- `TRANSLATE_ENABLED=true`
- `TRANSLATE_TARGET_LANG=kk`
- `TRANSLATION_CHUNK_SIZE=3000`
- `SEND_PHOTO_ENABLED=true`
- `TELEGRAM_NEWS_CHAT_ID`
- `TELEGRAM_DISABLE_LINKS=true`
- `OPENAI_API_KEY`
- `BOT_TOKEN`
- `ADMIN_ID`
- `NEWS_POST_MODE=admin|channel`
- `LOG_LEVEL`

## Админ-команды

- `/news_status`
- `/fetch_news_now`
- `/fetch_topic football|tennis|hockey|basketball`
- `/news_test`

## Основные модули

- `services/news_fetcher.py` — запросы к GNews.
- `services/article_extractor.py` — скачивание HTML, извлечение и очистка текста, fallback.
- `services/translator.py` — перевод на казахский чанками.
- `services/formatter.py` — сборка и разбиение Telegram-сообщений.
- `services/dedup.py` — нормализация заголовка и hash для антидубля.
- `services/news_repository.py` — SQLite-хранилище `news_sent` и счётчик API.
- `services/news_pipeline.py` — orchestration пайплайна и отправка в Telegram.

## Локальный запуск

```bash
pip install -r requirements.txt
python bot.py
```
