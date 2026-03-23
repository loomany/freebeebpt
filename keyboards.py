from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def analysis_cta_keyboard() -> InlineKeyboardMarkup:
    keyboard = InlineKeyboardMarkup()
    keyboard.add(InlineKeyboardButton("💸 Пополнить с кешбэком", callback_data="cashback_topup"))
    return keyboard


def topup_keyboard() -> InlineKeyboardMarkup:
    keyboard = InlineKeyboardMarkup()
    keyboard.add(InlineKeyboardButton("Пополнить", callback_data="start_topup"))
    return keyboard


def admin_news_review_keyboard(article_ref: str) -> InlineKeyboardMarkup:
    keyboard = InlineKeyboardMarkup(row_width=2)
    keyboard.add(
        InlineKeyboardButton("✅ Опубликовать", callback_data=f"send_news:{article_ref}"),
        InlineKeyboardButton("❌ Пропустить", callback_data=f"skip_news:{article_ref}"),
    )
    return keyboard
