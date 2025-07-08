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
            f"Пользователь прислал матч: \"{user_text}\".\n\n"
            f"Проанализируй матч как спортивный аналитик с опытом ставок. Составь полноценный разбор в формате статьи:\n\n"
            f"1. Введение: кто играет, турнир, дата\n"
            f"2. Форма команд: последние 5 игр, результативность, стиль игры\n"
            f"3. Очные встречи, мотивация, составы, тренеры\n"
            f"4. Что видно по линии и коэффициентам (если известно)\n"
            f"5. Сформируй мнение, кто сильнее\n"
            f"6. 💬 Затем выбери **одну конкретную ставку**, которую ты бы сделал сам (например: Тотал Больше 2.5, П1, ИТБ1 1.5, ОЗ, X2 и т.д.)\n"
            f"7. Укажи **коэффициент**, если видишь его на скрине, или оцени сам\n"
            f"8. Аргументируй, почему выбрал именно эту ставку\n"
            f"9. Заверши выводом: твой подход, риск, и почему эта ставка логична\n\n"
            f"Стиль — как спортивный блогер с опытом ставок: честно, уверенно, по делу. Добавь ⚽️🔥📊."
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

        cashback_text = (
            "💼 *Как пополнять счёт с выгодой?*\n\n"
            "Теперь ты можешь пополнить счёт в своём букмекерском аккаунте — *безопасно и с кешбэком* 💸\n\n"
            "Сервис **Paydala & Freebee** работает через официальные платёжные каналы и поддерживает:\n\n"
            "• Olimpbet  \n"
            "• 1xBet  \n"
            "• Parimatch  \n"
            "• Fonbet  \n\n"
            "🔒 *100% легально и проверено пользователями*  \n"
            "💸 Кешбэк возвращается на карту или логин\n\n"
            "📎 При желании, можем выдать реквизиты, схемы и партнёрский номер\n\n"
            "📲 Нажми ниже, если хочешь пополнить — и получить кешбэк"
        )

        keyboard = types.InlineKeyboardMarkup()
        keyboard.add(
            types.InlineKeyboardButton(
                "💬 Узнать подробнее",
                url="https://t.me/freebee_admin",
            )
        )

        await message.answer(cashback_text, reply_markup=keyboard, parse_mode="Markdown")

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
