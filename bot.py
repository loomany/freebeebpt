import asyncio
import os

from aiogram import Bot, Dispatcher, types
from aiogram.types import CallbackQuery
from aiogram.utils import executor
from dotenv import load_dotenv
from openai import AsyncOpenAI

from formatters.match_center_formatter import build_match_center_text
from keyboards import analysis_cta_keyboard, topup_keyboard
from services.match_data_service import MatchDataService

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
ADMIN_ID_RAW = os.getenv("ADMIN_ID")
ADMIN_ID = int(ADMIN_ID_RAW) if ADMIN_ID_RAW else None

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(bot)
client = AsyncOpenAI(api_key=OPENAI_API_KEY)
match_data_service = MatchDataService(client)

registered_users: set[int] = set()

TOPUP_INFO_TEXT = (
    "💼 Как пополнять счёт с выгодой?\n\n"
    "Теперь ты можешь пополнить счёт — безопасно и с кешбэком 💸\n\n"
    "• Кешбэк до 5%\n\n"
    "🔒 100% легально и проверено пользователями\n"
    "💸 Кешбэк возвращается талонами на бензин\n\n"
    "📲 Нажми ниже, если хочешь пополнить — и получить кешбэк"
)

TOPUP_START_TEXT = (
    "Напишите сумму, букмекера и удобный способ оплаты, "
    "и мы подскажем, как пополнить счёт с кешбэком."
)


@dp.message_handler(commands=["start"])
async def start_handler(message: types.Message):
    user = message.from_user

    if user.id not in registered_users:
        registered_users.add(user.id)
        text = (
            "📥 Новый пользователь зарегистрировался!\n\n"
            f"👤 Имя: {user.first_name or ''} {user.last_name or ''}\n"
            f"🆔 ID: {user.id}\n"
            f"💬 Username: @{user.username or '—'}\n"
            f"🌍 Язык: {user.language_code or 'неизвестен'}"
        )
        if ADMIN_ID is not None:
            await bot.send_message(chat_id=ADMIN_ID, text=text)

    await message.answer(
        "🤖 Добро пожаловать! Я ИИ-бот для глубокого разбора матчей.\n"
        "Отправь название матча или скриншот — и я подготовлю структурированный Match Center без ставок и коэффициентов. ⚽📊"
    )


@dp.callback_query_handler(lambda c: c.data == "cashback_topup")
async def cashback_topup_handler(callback_query: CallbackQuery):
    await callback_query.answer()
    await callback_query.message.answer(TOPUP_INFO_TEXT, reply_markup=topup_keyboard())


@dp.callback_query_handler(lambda c: c.data == "start_topup")
async def start_topup_handler(callback_query: CallbackQuery):
    await callback_query.answer()
    await callback_query.message.answer(TOPUP_START_TEXT)


@dp.message_handler(content_types=[types.ContentType.TEXT, types.ContentType.PHOTO])
async def handle_input(message: types.Message):
    await message.answer("🧠 Собираю расширенный анализ матча...")

    try:
        match_info = await match_data_service.resolve_match_from_image(message)
        if not match_info or not match_info.get("home_team") or not match_info.get("away_team"):
            await message.answer(
                "Не удалось распознать матч на скрине. Попробуйте отправить более четкий скрин, где видны команды и время матча."
            )
            return

        match_data = await match_data_service.get_match_full_data(match_info)
        result = build_match_center_text(match_data)
        await message.answer(result, reply_markup=analysis_cta_keyboard())

    except Exception as error:
        await message.answer(
            "Не удалось распознать матч на скрине. Попробуйте отправить более четкий скрин, где видны команды и время матча."
        )
        print("Match analysis error:", error)


if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    try:
        executor.start_polling(dp, skip_updates=True)
    finally:
        loop.run_until_complete(bot.session.close())
        loop.run_until_complete(client.aclose())
        loop.close()
