import os
from aiogram import Bot, Dispatcher, types
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
from aiogram.utils import executor
from dotenv import load_dotenv
import openai
import asyncio

# Загружаем токены из .env
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

# Настройка Telegram-бота
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(bot)

# Настройка OpenAI
openai.api_key = OPENAI_API_KEY

# Клавиатура
keyboard = ReplyKeyboardMarkup(resize_keyboard=True)
keyboard.add(KeyboardButton("📊 Получить прогноз"))

# Команда /start
@dp.message_handler(commands=["start"])
async def start_handler(message: types.Message):
    text = (
        "🤖 Тебя приветствует ИИ-бот, который может давать прогнозы "
        "на любое спортивное событие.\n\n"
        "Напиши, что тебя интересует, или просто сделай скрин с командами — "
        "и получи бесплатный прогноз от искусственного интеллекта.\n\n"
        "👇 Нажми кнопку ниже 👇"
    )
    await message.answer(text, reply_markup=keyboard)

# Обработка кнопки
@dp.message_handler(lambda message: message.text == "📊 Получить прогноз")
async def handle_prediction(message: types.Message):
    await message.answer("⏳ Генерирую прогноз от ИИ...")

    # Простой промпт — можно улучшать
    gpt_prompt = (
        "Сделай краткий прогноз на одно из ближайших популярных спортивных событий "
        "(футбол, теннис, баскетбол), включая команду, ставку, коэффициент и 2-3 причины."
    )

    try:
        response = openai.ChatCompletion.create(
            model="gpt-4o",  # Самая новая версия
            messages=[
                {"role": "system", "content": "Ты спортивный аналитик с опытом и чувством юмора."},
                {"role": "user", "content": gpt_prompt}
            ],
            temperature=1.0,
            max_tokens=500
        )
        result = response.choices[0].message["content"]
        await message.answer(result)

    except Exception as e:
        await message.answer("❌ Ошибка при получении прогноза от ИИ.")
        print("OpenAI error:", e)

# Запуск
if __name__ == "__main__":
    executor.start_polling(dp, skip_updates=True)
