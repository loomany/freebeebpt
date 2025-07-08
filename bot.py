import os
from aiogram import Bot, Dispatcher, types
from aiogram.utils import executor
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(bot)
client = OpenAI(api_key=OPENAI_API_KEY)

# ID администратора для уведомлений
ADMIN_ID = 200082134

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
        "🤖 Тебя приветствует ИИ-бот, который может давать прогнозы на любое спортивное событие.\n\n"
        "Отправь текст или скрин с матчем, и я запущу анализ от имени спортивного аналитика."
    )

# Обработка текста или скрина
@dp.message_handler(content_types=[types.ContentType.TEXT, types.ContentType.PHOTO])
async def handle_input(message: types.Message):
    await message.answer("🧠 Запускаю анализ...")

    # Если пришёл скрин — ставим placeholder, пока не реализован OCR
    if message.photo:
        user_prompt = "На изображении содержится скрин матча. Сгенерируй прогноз, предполагая, что пользователь интересуется этим матчем."
    else:
        user_prompt = message.text.strip()

    prompt = (
        f"Ты профессиональный спортивный аналитик.\n"
        f"Пользователь интересуется матчем или просит прогноз. Запрос:\n"
        f"\"{user_prompt}\"\n\n"
        f"Сделай подробный прогноз, включая:\n"
        f"- матч, дату и турнир\n"
        f"- ставку и рекомендуемый маркет (например: П1, Тотал Больше 2.5)\n"
        f"- текущие коэффициенты (если можешь предположить)\n"
        f"- форму команд (5 последних матчей)\n"
        f"- H2H (личные встречи)\n"
        f"- ключевых игроков, травмы или дисквалификации\n"
        f"- уверенный вывод (можно с ⚽️📊🔥)\n"
        f"\nЕсли данных мало — используй обоснованные домыслы, но не выдумывай матч полностью."
    )

    try:
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": "Ты честный спортивный аналитик с глубоким пониманием футбола, статистики и формы команд. Не пиши чушь."},
                {"role": "user", "content": prompt}
            ],
            temperature=1.0,
            max_tokens=800
        )
        result = response.choices[0].message.content
        await message.answer(result)

    except Exception as e:
        await message.answer("❌ Ошибка при получении прогноза от ИИ.")
        await message.answer(f"📛 Подробности: {str(e)}")
        print("OpenAI error:", e)

# Старт
if __name__ == "__main__":
    executor.start_polling(dp, skip_updates=True)
