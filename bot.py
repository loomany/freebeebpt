from __future__ import annotations

import asyncio
import logging
import os

from aiogram import Bot, Dispatcher, types
from aiogram.utils import executor
from dotenv import load_dotenv
from openai import AsyncOpenAI

from handlers.admin_news import register_admin_news_handlers
from scheduler.news_scheduler import run_hourly_news_scheduler
from services.ai_news_processor import AINewsProcessor
from services.gnews_service import GNewsService
from services.news_formatter import NewsFormatter
from services.news_pipeline import NewsPipeline
from services.news_ranker import NewsRanker
from services.news_repository import NewsRepository

load_dotenv()
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
ADMIN_ID_RAW = os.getenv("ADMIN_ID")
ADMIN_ID = int(ADMIN_ID_RAW) if ADMIN_ID_RAW else None
NEWS_CHANNEL_ID = os.getenv("TELEGRAM_NEWS_CHAT_ID") or os.getenv("NEWS_CHANNEL_ID")
NEWS_POST_MODE = os.getenv("NEWS_POST_MODE", "admin")
GNEWS_API_KEY = os.getenv("GNEWS_API_KEY")

if not GNEWS_API_KEY:
    logger.error("GNEWS_API_KEY не задан")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(bot)
client = AsyncOpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None
repository = NewsRepository()
gnews_service = GNewsService(repository=repository, api_key=GNEWS_API_KEY)
ai_processor = AINewsProcessor(client=client)
ranker = NewsRanker(min_score=int(os.getenv("NEWS_IMPORTANCE_MIN_SCORE", "75")))
formatter = NewsFormatter()
news_pipeline = NewsPipeline(
    bot=bot,
    repository=repository,
    gnews_service=gnews_service,
    formatter=formatter,
    ai_processor=ai_processor,
    ranker=ranker,
    admin_id=ADMIN_ID,
    news_channel_id=NEWS_CHANNEL_ID,
    news_post_mode=NEWS_POST_MODE,
)

register_admin_news_handlers(
    dp,
    context={
        "repository": repository,
        "gnews_service": gnews_service,
        "formatter": formatter,
        "ai_processor": ai_processor,
        "ranker": ranker,
        "news_pipeline": news_pipeline,
        "news_post_mode": NEWS_POST_MODE,
    },
)


@dp.message_handler(commands=["start"])
async def start_handler(message: types.Message):
    await message.answer(
        "Привет! Это спортивный новостной бот.\n"
        "Доступные админ-команды: /news_status, /fetch_news_now, /fetch_topic, /news_test, /news_test_ai, /news_test_raw, /news_test_compare"
    )


async def on_startup(_: Dispatcher) -> None:
    if os.getenv("NEWS_ENABLED", "true").lower() == "true":
        asyncio.create_task(run_hourly_news_scheduler(news_pipeline))


if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    try:
        executor.start_polling(dp, skip_updates=True, on_startup=on_startup)
    finally:
        loop.run_until_complete(bot.session.close())
        if client:
            loop.run_until_complete(client.close())
        loop.close()
