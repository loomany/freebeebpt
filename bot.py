from __future__ import annotations

import asyncio
import logging
import os

from aiogram import Bot, Dispatcher, types
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from aiogram.utils import executor
from dotenv import load_dotenv
from openai import AsyncOpenAI

from handlers.admin_news import register_admin_news_handlers
from scheduler.news_scheduler import run_hourly_news_scheduler
from services.ai_news_processor import AINewsProcessor
from services.fal_image_service import FalImageService
from services.gnews_service import GNewsService
from services.news_formatter import NewsFormatter
from services.news_pipeline import NewsPipeline
from services.news_ranker import NewsRanker
from services.news_repository import NewsRepository
from services.telegram_publisher import TelegramPublisher

load_dotenv()
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
ADMIN_ID_RAW = os.getenv("ADMIN_ID") or os.getenv("ADMIN_USER_ID")
ADMIN_ID = int(ADMIN_ID_RAW) if ADMIN_ID_RAW else None
NEWS_CHANNEL_ID = os.getenv("TELEGRAM_NEWS_CHAT_ID") or os.getenv("NEWS_CHANNEL_ID")
logger.info("TELEGRAM_NEWS_CHAT_ID loaded as %r", NEWS_CHANNEL_ID)
NEWS_POST_MODE = os.getenv("NEWS_POST_MODE", "admin")
GNEWS_API_KEY = os.getenv("GNEWS_API_KEY")

if not GNEWS_API_KEY:
    logger.error("GNEWS_API_KEY не задан")


def validate_runtime_config() -> None:
    news_enabled = os.getenv("NEWS_ENABLED", "true").lower() == "true"
    if not BOT_TOKEN:
        logger.error("BOT_TOKEN не задан")
    if not ADMIN_ID and NEWS_POST_MODE == "admin":
        logger.error("ADMIN_ID не задан при NEWS_POST_MODE=admin: новости некому отправлять")
    if NEWS_POST_MODE == "channel" and not NEWS_CHANNEL_ID:
        logger.error("TELEGRAM_NEWS_CHAT_ID/NEWS_CHANNEL_ID не задан при NEWS_POST_MODE=channel")
    if not news_enabled:
        logger.warning("NEWS_ENABLED=false: планировщик новостей отключен")
    logger.info(
        "[NEWS CONFIG] enabled=%s mode=%s admin_id=%s channel_id=%r poll_minutes=%s",
        news_enabled,
        NEWS_POST_MODE,
        ADMIN_ID,
        NEWS_CHANNEL_ID,
        os.getenv("NEWS_POLL_MINUTES", "15"),
    )

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(bot, storage=MemoryStorage())
client = AsyncOpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None
repository = NewsRepository()
gnews_service = GNewsService(repository=repository, api_key=GNEWS_API_KEY)
ai_processor = AINewsProcessor(client=client)
ranker = NewsRanker(
    min_score=int(os.getenv("NEWS_IMPORTANCE_MIN_SCORE", os.getenv("PRESCORE", "75"))),
    admin_preview_min_score=int(os.getenv("ADMIN_PREVIEW_MIN_SCORE", os.getenv("NEWS_IMPORTANCE_MIN_SCORE", os.getenv("PRESCORE", "75")))),
)
formatter = NewsFormatter()
fal_image_service = FalImageService()
telegram_publisher = TelegramPublisher(bot)
news_pipeline = NewsPipeline(
    bot=bot,
    repository=repository,
    gnews_service=gnews_service,
    formatter=formatter,
    ai_processor=ai_processor,
    ranker=ranker,
    telegram_publisher=telegram_publisher,
    fal_image_service=fal_image_service,
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
        "telegram_publisher": telegram_publisher,
        "news_channel_id": NEWS_CHANNEL_ID,
        "fal_image_service": fal_image_service,
    },
)


@dp.message_handler(commands=["start"])
async def start_handler(message: types.Message):
    await message.answer(
        "Привет! Это спортивный новостной бот.\n"
        "Доступные админ-команды: /news_status, /fetch_news_now, /fetch_topic, /test_channel, /news_test, /news_test_ai, /news_test_image, /news_test_full, /news_test_preview, /news_debug_last, /last_preview_status, /news_test_raw, /news_test_compare, /new_state, /cancel"
    )


async def on_startup(_: Dispatcher) -> None:
    validate_runtime_config()
    if NEWS_CHANNEL_ID:
        access_ok, access_message = await telegram_publisher.verify_channel_access(NEWS_CHANNEL_ID)
        logger.info("[STARTUP CHANNEL CHECK] ok=%s chat_id=%s message=%s", access_ok, NEWS_CHANNEL_ID, access_message)
    else:
        logger.warning("[STARTUP CHANNEL CHECK] TELEGRAM_NEWS_CHAT_ID is empty")
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
