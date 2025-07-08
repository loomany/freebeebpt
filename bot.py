import os
from aiogram import Bot, Dispatcher, types
from aiogram.utils import executor
from dotenv import load_dotenv
from openai import OpenAI

# Загрузка переменных окружения
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

# Настройка Telegram и OpenAI
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(bot)
client = OpenAI(api_key=OPENAI_API_KEY)

# Команда /start
@dp.message_handler(commands=["start"])
async def start_handler(message: types.Message):
    text = (
        "🤖 Тебя приветствует ИИ-бот, который может давать прогнозы "
        "на любое спортивное событие.\n\n"
        "Напиши, что тебя интересует, или просто сделай скрин с командами — "
        "и получи бесплатный прогноз от искусственного интеллекта."
    )
    await message.answer(text)

# Обработка любого текстового сообщения
@dp.message_handler(content_types=types.ContentType.TEXT)
async def handle_message(message: types.Message):
    await message.answer("⏳ Генерирую прогноз от ИИ...")

    user_input = message.text.strip()

    prompt = (
        f"Сделай краткий прогноз на спортивное событие, о котором говорит пользователь:\n"
        f"\"{user_input}\"\n"
        "Укажи матч, ставку, коэффициент и 2-3 причины. Стиль — аналитика с эмоцией."
    )

    try:
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": "Ты спортивный аналитик с опытом и чувством юмора."},
                {"role": "user", "content": prompt}
            ],
            temperature=1.0,
            max_tokens=500
        )
        result = response.choices[0].message.content
        await message.answer(result)

    except Exception as e:
        await message.answer("❌ Ошибка при получении прогноза от ИИ.")
        await message.answer(f"📛 Подробности: {str(e)}")
        print("OpenAI error:", e)

# Запуск
if __name__ == "__main__":
    executor.start_polling(dp, skip_updates=True)
