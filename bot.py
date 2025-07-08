import os
import base64
from io import BytesIO
from aiogram import Bot, Dispatcher, types
from aiogram.utils import executor
from dotenv import load_dotenv
import asyncio
from openai import AsyncOpenAI

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
# ID администратора для уведомлений
ADMIN_ID = int(os.getenv("ADMIN_ID"))

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(bot)
client = AsyncOpenAI(api_key=OPENAI_API_KEY)

# Хранилище уже зарегистрированных пользователей
registered_users: set[int] = set()

# Приветствие
@dp.message_handler(commands=["start"])

async def start_handler(message: types.Message):
    user = message.from_user

    if user.id not in registered_users:
        registered_users.add(user.id)
        text = (
            "📥 Новый пользователь зарегистрировался!\n\n"
            f"👤 Имя: {user.first_name or ''} {user.last_name or ''}\n"
            f"🆔 ID: {user.id}\n"
            f"💬 Username: @{user.username if user.username else '—'}\n"
            f"🌍 Язык: {user.language_code or 'неизвестен'}"
        )
        await bot.send_message(chat_id=ADMIN_ID, text=text)

    await message.answer(
        "🤖 Добро пожаловать! Я ИИ-бот для глубокого разбора матчей.\n"
        "Отправь название матча или скриншот — и я дам развёрнутую аналитику как статья, с прогнозом. ⚽📊🔥"
    )

# Обработка текста или скрина
@dp.message_handler(content_types=[types.ContentType.TEXT, types.ContentType.PHOTO])
async def handle_input(message: types.Message):
    await message.answer("🧠 Анализирую... Сейчас будет мощный разбор 👇")

    user_text = (message.caption or message.text or "").strip()
    user_content = []

    if user_text:
        user_content.append({"type": "text", "text": (
            f"Пользователь прислал матч: \"{user_text}\".\n"
            f"Проанализируй его как спортивный аналитик. Сделай развёрнутую статью с:\n"
            f"1. Интро — кто играет, когда, турнир\n"
            f"2. Текущая форма обеих команд (последние 5 игр)\n"
            f"3. Тактический разбор, H2H, мотивация\n"
            f"4. Информация о составе, травмах, стиле игры\n"
            f"5. Что говорит линия (если видно) — кто фаворит\n"
            f"6. 💡 Сформируй мнение: кого бы ты поддержал\n"
            f"7. Дай свой прогноз на исход — чётко: П1, ТБ 2.5, Х2 и т.п.\n"
            f"8. Заверши статью выводом и предупреждением о рисках\n\n"
            f"Добавляй ⚽️📊🔥 где нужно. Пиши как для спортивного блога или колонки."
        )})

    if message.photo:
        photo = message.photo[-1]
        buffer = BytesIO()
        await photo.download(destination=buffer)
        buffer.seek(0)
        img_b64 = base64.b64encode(buffer.read()).decode()
        user_content.append({
            "type": "image_url",
            "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"}
        })

    messages = [
        {
            "role": "system",
            "content": "Ты профессиональный спортивный аналитик с живым стилем. Пиши как для блога: аргументируй, не мямли, используй факты, стройную структуру. Дай своё мнение."
        },
        {
            "role": "user",
            "content": user_content
        }
    ]

    try:
        response = await client.chat.completions.create(
            model="gpt-4o",
            messages=messages,
            temperature=1.3,
            max_tokens=2000
        )
        result = response.choices[0].message.content
        await message.answer(result)

    except Exception as e:
        await message.answer("❌ Ошибка при получении прогноза от ИИ.")
        await message.answer(f"📛 Подробности: {str(e)}")
        print("OpenAI error:", e)

# Старт
if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    try:
        executor.start_polling(dp, skip_updates=True)
    finally:
        loop.run_until_complete(bot.session.close())
        loop.run_until_complete(client.aclose())
        loop.close()
