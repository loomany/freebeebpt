# FreeBeeBPT News Bot

Telegram-бот публикует только англоязычные спортивные новости из GNews API по 4 темам: football, tennis, hockey, basketball. Новости кратко переводятся на русский и казахский через OpenAI и отправляются в Telegram.

## Переменные окружения

- `GNEWS_API_KEY`
- `OPENAI_API_KEY`
- `BOT_TOKEN`
- `ADMIN_ID`
- `NEWS_CHANNEL_ID`
- `NEWS_POST_MODE=admin|channel` (по умолчанию `admin`)
- `LOG_LEVEL`

Если `GNEWS_API_KEY` не задан, бот логирует: `GNEWS_API_KEY не задан`.

## Используемый endpoint

Основной endpoint:

```text
GET https://gnews.io/api/v4/search?q={topic}&lang=en&max=10&sortby=publishedAt&page=1&apikey=...
```

Темы первого этапа:
- `football`
- `tennis`
- `hockey`
- `basketball`

## Лимит запросов

- Планировщик запускает один цикл каждый час.
- Внутри цикла выполняются 4 запроса: по одному на каждую тему.
- Это даёт `4 × 24 = 96` запросов в сутки.
- В SQLite хранится суточный счётчик запросов.
- Hard stop: `96` запросов в сутки, чтобы сохранить запас для ручных запусков.

## Дедупликация

SQLite база: `data/news_bot.sqlite3`.

Таблица `news_posts` хранит:
- тему,
- URL,
- заголовок,
- время публикации,
- источник,
- тексты RU/KK,
- статус (`new`, `posted`, `queued`, `failed`).

Ключ дедупликации:
1. `url`
2. fallback: `sha256(title + publishedAt)`

## Админ-команды

- `/news_status`
- `/fetch_news_now`
- `/fetch_topic football`
- `/fetch_topic tennis`
- `/fetch_topic hockey`
- `/fetch_topic basketball`
- `/test_news_format`

## Локальный запуск

```bash
pip install -r requirements.txt
python bot.py
```

## Ручное тестирование

1. Заполнить `.env` нужными ключами.
2. Запустить `python bot.py`.
3. В Telegram вызвать `/news_status`.
4. Выполнить `/fetch_news_now` или `/fetch_topic football`.
5. Проверить, что новые новости ушли админу или в канал в зависимости от `NEWS_POST_MODE`.
6. Повторно вызвать команду и убедиться, что дубликаты не публикуются.

## Пример поста

```text
📰 Football

🇷🇺 Мбаппе может пропустить следующий матч из-за повреждения.

🇰🇿 Мбаппе жарақатына байланысты келесі матчты өткізіп алуы мүмкін.

🔗 ESPN
https://example.com/story
```
