from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def analysis_cta_keyboard() -> InlineKeyboardMarkup:
    keyboard = InlineKeyboardMarkup()
    keyboard.add(InlineKeyboardButton("💸 Пополнить с кешбэком", callback_data="cashback_topup"))
    return keyboard


def topup_keyboard() -> InlineKeyboardMarkup:
    keyboard = InlineKeyboardMarkup()
    keyboard.add(InlineKeyboardButton("Пополнить", callback_data="start_topup"))
    return keyboard
