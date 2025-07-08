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
ADMIN_ID = int(os.getenv("ADMIN_ID"))

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(bot)
client = AsyncOpenAI(api_key=OPENAI_API_KEY)

registered_users: set[int] = set()

# 🔹 Отправка разбитого текста + кешбэк только в конце
async def split_and_send_with_cashback(full_text: str, message: types.Message):
    max_length = 4000

    cashback_text = (
        "\n\n💼 *Как пополнять счёт с выгодой?*\n\n"
        "Теперь ты можешь пополнить счёт в своём букмекерском аккаунте — *безопасно и с кешбэком* 💸\n\n"
        "Сервис **Paydala & Freebee** работает через официальные платёжные каналы и поддерживает:\n\n"
        "• Olimpbet\n• 1xBet\n• Parimatch\n• Fonbet\n\n"
        "🔒 *100% легально и проверено пользователями*\n"
        "💸 Кешбэк возвращается на карту или логин\n\n"
        "📎 При желании, можем выдать реквизиты, схемы и партнёрский номер\n\n"
        "📲 Нажми ниже, чтобы узнать подробнее"
    )

    keyboard = types.InlineKeyboardMarkup()
    keyboard.add(
        types.InlineKeyboardButton("💬 Узнать подробнее", url="https://t.me/freebee_admin")
    )

    chunks = [full_text[i:i + max_length] for i in range(0, len(full_text), max_length)]

    for i, chunk in enumerate(chunks):
        if i == len(chunks) - 1:
            await message.answer(chunk + cashback_text, reply_markup=keyboard, parse_mode="Markdown")
        else:
            await message.answer(chunk)

# /start + регистрация
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
        await bot.send_message(chat_id=ADMIN_ID, text=text)

    await message.answer(
        "🤖 Добро пожаловать! Я ИИ-бот для глубокого разбора матчей.\n"
        "Отправь название матча или скриншот — и я подготовлю развёрнутую аналитику как статью. ⚽📊🔥"
    )

# Анализ текста или фото
@dp.message_handler(content_types=[types.ContentType.TEXT, types.ContentType.PHOTO])
async def handle_input(message: types.Message):
    await message.answer("🧠 Запускаю расширенный анализ...")

    user_text = (message.caption or message.text or "").strip()
    user_content = []

    if user_text:
        user_content.append({"type": "text", "text": (
            f"Пользователь прислал матч: \"{user_text}\".\n\n"
            f"Сделай аналитическую статью:\n"
            f"1. Кто играет, дата, турнир\n"
            f"2. Форма команд (последние 5 игр)\n"
            f"3. Очные встречи, стиль, мотивация\n"
            f"4. Составы, травмы\n"
            f"5. Линия: что говорят коэффициенты\n"
            f"6. 💬 Итог: кого бы ты выбрал лично\n"
            f"7. 🎯 Исход (например: П1, Тотал Больше 2.5, ОЗ)\n"
            f"8. Заключение — стоит ли играть, и с каким риском\n\n"
            f"Формат — как спортивная статья. Стиль уверенный. Используй ⚽📊🔥."
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
            "content": "Ты спортивный аналитик. Пиши как для блога: уверенно, с выводами, ставкой и рекомендацией."
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
        await split_and_send_with_cashback(result, message)

    except Exception as e:
        await message.answer("❌ Ошибка при анализе.")
        await message.answer(f"📛 Детали: {e}")
        print("OpenAI error:", e)

if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    try:
        executor.start_polling(dp, skip_updates=True)
    finally:
        loop.run_until_complete(bot.session.close())
        loop.run_until_complete(client.aclose())
        loop.close()
