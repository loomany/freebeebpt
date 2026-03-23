# FreeBeeBPT News Bot

Telegram-бот получает спортивные новости из GNews, извлекает полный текст статьи, прогоняет материал через OpenAI-фильтр, генерирует новую вертикальную картинку через fal Nano Banana и отправляет в Telegram только AI-текст + AI-картинку.

## Пайплайн

1. Планировщик запускает цикл по `NEWS_POLL_MINUTES`.
2. При `NEWS_TOPIC_ROTATION=true` за цикл проверяется только одна тема по кругу (`football`, `tennis`, `hockey`, `basketball`).
3. Бот получает до `GNEWS_MAX_RESULTS` новостей из GNews.
4. Перед AI-обработкой бот сначала режет дубли прямо в ответе GNews по canonical URL / fingerprint заголовка, а потом проверяет антидубль по `article_hash`, `normalized_title` и похожим заголовкам в SQLite: в БД сохраняются `article_hash`, `url`, `status`, `sent_to_channel`; после рестарта эти записи повторно читаются из БД.
5. Для новой статьи бот пытается извлечь полный текст через `trafilatura`, затем через `readability-lxml + BeautifulSoup`, затем через простой HTML parse.
6. Если полный текст короткий или недоступен, используется fallback из `content`, либо `title + description`.
7. OpenAI возвращает строго JSON с фильтром важности, казахским summary, ключевыми тезисами, optional betting impact-блоком и `image_prompt_en`.
8. Для важных новостей бот вызывает fal Nano Banana c `aspect_ratio=9:16` и получает новую картинку.
9. В Telegram уходит только сгенерированная картинка и AI-текст без source URL и без image из news API.
10. Бот отправляет только новости с `importance_score >= NEWS_IMPORTANCE_MIN_SCORE` и `importance_level in {high, top}`.

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
- `OPENAI_API_KEY`
- `OPENAI_ENABLED=true`
- `OPENAI_MODEL=gpt-5.4`
- `NEWS_IMPORTANCE_MIN_SCORE=75`
- `FAL_KEY`
- `FAL_MODEL=fal-ai/nano-banana-2`
- `FAL_IMAGE_ASPECT_RATIO=9:16`
- `FAL_IMAGE_NUM=1`
- `FAL_IMAGE_OUTPUT_FORMAT=png`
- `FAL_IMAGE_RESOLUTION=2K`
- `FAL_SAFETY_TOLERANCE=4`
- `FAL_TIMEOUT_SECONDS=90`
- `BOT_TOKEN`
- `ADMIN_ID`
- `TELEGRAM_NEWS_CHAT_ID`
- `TELEGRAM_DISABLE_LINKS=true`
- `REQUIRE_IMAGE_FOR_NEWS_POST=true`
- `SEND_TEXT_IF_IMAGE_FAIL=false`
- `USE_NEWS_API_IMAGES=false`
- `NEWS_POST_MODE=admin|channel`
- `LOG_LEVEL`
- `NEWS_DB_PATH` — путь к SQLite БД. Для Railway укажи путь внутри persistent volume, например `${RAILWAY_VOLUME_MOUNT_PATH}/news_bot.sqlite3`.

## Админ-команды

- `/news_status`
- `/fetch_news_now`
- `/fetch_topic football|tennis|hockey|basketball`
- `/news_test`
- `/news_test_ai`
- `/news_test_image`
- `/news_test_full`
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

## Дедупликация и restart-safe хранение

- Источник истины для дедупликации — таблица `news_sent` в SQLite, а не память процесса.
- Ещё до AI-обработки бот отбрасывает дубли внутри одного ответа GNews: нормализует URL, убирает tracking query params и сравнивает fingerprint заголовка.
- При fetch каждая новая статья сохраняется в БД с `article_hash` и `url`; после успешной отправки запись обновляется с `status=posted` и `sent_to_channel=1`.
- Если разные источники приносят один и тот же инфоповод с разными URL, бот дополнительно сравнивает похожесть заголовков и режет такие дубли до публикации.
- Перед публикацией пайплайн повторно читает состояние статьи из БД и пропускает публикацию, если запись уже была отправлена (`sent_to_channel=1` или `status=posted`).
- На Railway SQLite-файл нужно хранить только в persistent volume. Используй `NEWS_DB_PATH=${RAILWAY_VOLUME_MOUNT_PATH}/news_bot.sqlite3` или аналогичный путь внутри volume.
