from __future__ import annotations

import logging
import os
from typing import Any

from aiogram import Dispatcher, types
from aiogram.dispatcher import FSMContext
from aiogram.utils.exceptions import MessageNotModified

from services.gnews_service import TOPICS
from states import ManualNewsState

logger = logging.getLogger(__name__)


def register_admin_news_handlers(dp: Dispatcher, *, context: dict[str, Any]) -> None:
    repository = context["repository"]
    gnews_service = context["gnews_service"]
    news_pipeline = context["news_pipeline"]
    news_post_mode = context["news_post_mode"]
    telegram_publisher = context["telegram_publisher"]
    news_channel_id = context["news_channel_id"]

    async def _ensure_admin(message: types.Message) -> bool:
        admin_id_raw = os.getenv("ADMIN_ID") or os.getenv("ADMIN_USER_ID")
        if not admin_id_raw:
            await message.answer("ADMIN_ID/ADMIN_USER_ID не задан")
            return False
        if str(message.from_user.id) != admin_id_raw:
            await message.answer("Команда доступна только администратору")
            return False
        return True

    @dp.message_handler(commands=["news_status"])
    async def news_status_handler(message: types.Message):
        if not await _ensure_admin(message):
            return
        stats = repository.get_stats()
        await message.answer(
            "\n".join(
                [
                    f"mode: {news_post_mode}",
                    f"api key configured: {'yes' if gnews_service.configured else 'no'}",
                    f"today_requests: {repository.get_daily_requests()}",
                    f"last_fetch_time: {stats['last_fetch_time'] or 'never'}",
                    f"last_topic: {stats['last_topic'] or 'n/a'}",
                    f"db_path: {stats['db_path']}",
                    f"total_saved_articles: {stats['total_saved_articles']}",
                    f"total_posted_articles: {stats['total_posted_articles']}",
                    f"total_failed_articles: {stats['total_failed_articles']}",
                ]
            )
        )

    @dp.message_handler(commands=["new_state"])
    async def new_state_handler(message: types.Message, state: FSMContext):
        if not await _ensure_admin(message):
            return
        logger.info("[MANUAL NEWS] command received admin_id=%s", message.from_user.id)
        await ManualNewsState.waiting_for_text.set()
        logger.info("[MANUAL NEWS] waiting for text")
        await message.answer("Отправьте текст новости одним сообщением")

    @dp.message_handler(commands=["cancel"], state="*")
    async def cancel_manual_news_handler(message: types.Message, state: FSMContext):
        if not await _ensure_admin(message):
            return
        current_state = await state.get_state()
        if current_state != ManualNewsState.waiting_for_text.state:
            await message.answer("Нет активного режима ожидания новости")
            return
        await state.finish()
        await message.answer("Ввод ручной новости отменён")

    @dp.message_handler(state=ManualNewsState.waiting_for_text, content_types=types.ContentTypes.TEXT)
    async def manual_news_text_handler(message: types.Message, state: FSMContext):
        if not await _ensure_admin(message):
            return
        text = (message.text or "").strip()
        if text.startswith("/cancel"):
            await cancel_manual_news_handler(message, state)
            return
        if not text or text == "/new_state":
            await message.answer("Текст новости пуст")
            return
        await state.finish()
        status_message = await message.answer("Обрабатываю ручную новость...")
        try:
            result = await news_pipeline.process_manual_news(text)
        except Exception as error:  # noqa: BLE001
            logger.exception("[MANUAL NEWS] processing failed")
            await status_message.edit_text(f"Ошибка обработки ручной новости: {error}")
            return

        if result == "review_pending":
            await status_message.edit_text("Preview ручной новости отправлен")
            return
        await status_message.edit_text(f"Ручная новость обработана со статусом: {result}")

    @dp.message_handler(commands=["fetch_news_now"])
    async def fetch_news_now_handler(message: types.Message):
        if not await _ensure_admin(message):
            return
        await message.answer("Запускаю ручной цикл новостей...")
        summary = await news_pipeline.run_fetch_cycle(trigger="manual")
        await message.answer(summary)

    @dp.message_handler(commands=["fetch_topic"])
    async def fetch_topic_handler(message: types.Message):
        if not await _ensure_admin(message):
            return
        parts = (message.text or "").split(maxsplit=1)
        if len(parts) < 2 or parts[1].strip() not in TOPICS:
            await message.answer("Использование: /fetch_topic football|tennis|hockey|basketball")
            return
        topic = parts[1].strip()
        await message.answer(f"Запускаю тему {topic}...")
        summary = await news_pipeline.run_single_topic_cycle(topic, trigger="manual")
        await message.answer(summary)

    @dp.message_handler(commands=["test_channel"])
    async def test_channel_handler(message: types.Message):
        if not await _ensure_admin(message):
            return
        channel_chat_id = news_channel_id
        env_value = os.getenv("TELEGRAM_NEWS_CHAT_ID")
        await message.answer(
            f"Проверяю канал. TELEGRAM_NEWS_CHAT_ID={env_value!r}; target_chat_id={channel_chat_id!r}"
        )
        access_ok, access_message = await telegram_publisher.verify_channel_access(channel_chat_id)
        publish_result = await telegram_publisher.publish_news_post(
            chat_id=channel_chat_id,
            messages=["test"],
            image_url=None,
            article_title="/test_channel",
        )
        await message.answer(
            "\n".join(
                [
                    f"channel_access={access_ok}",
                    f"channel_check={access_message}",
                    f"status={publish_result.status}",
                    f"error={publish_result.error or 'none'}",
                    f"chat_id={publish_result.chat_id}",
                ]
            )
        )

    @dp.message_handler(commands=["news_test"])
    async def news_test_handler(message: types.Message):
        if not await _ensure_admin(message):
            return
        await message.answer("Запускаю debug-пайплайн news_test...")
        summary = await news_pipeline.run_news_test()
        await message.answer(summary)

    @dp.message_handler(commands=["news_test_ai"])
    async def news_test_ai_handler(message: types.Message):
        if not await _ensure_admin(message):
            return
        await message.answer("Запускаю AI-тест новости...")
        summary = await news_pipeline.run_news_test_ai()
        await message.answer(summary)

    @dp.message_handler(commands=["news_test_image"])
    async def news_test_image_handler(message: types.Message):
        if not await _ensure_admin(message):
            return
        await message.answer("Запускаю image-тест новости...")
        summary = await news_pipeline.run_news_test_image()
        await message.answer(summary)

    @dp.message_handler(commands=["news_test_full", "news_test_preview"])
    async def news_test_full_handler(message: types.Message):
        if not await _ensure_admin(message):
            return
        await message.answer("Запускаю полный preview news pipeline...")
        summary = await news_pipeline.run_news_test_full()
        await message.answer(summary)

    @dp.message_handler(commands=["news_test_raw"])
    async def news_test_raw_handler(message: types.Message):
        if not await _ensure_admin(message):
            return
        summary = await news_pipeline.run_news_test_raw()
        await message.answer(summary)

    @dp.message_handler(commands=["news_test_compare"])
    async def news_test_compare_handler(message: types.Message):
        if not await _ensure_admin(message):
            return
        summary = await news_pipeline.run_news_test_compare()
        await message.answer(summary)

    @dp.message_handler(commands=["news_debug_last", "last_preview_status"])
    async def news_debug_last_handler(message: types.Message):
        if not await _ensure_admin(message):
            return
        summary = await news_pipeline.get_last_debug_status()
        await message.answer(summary)

    @dp.callback_query_handler(lambda call: (call.data or "").startswith("send_news:"))
    async def send_news_to_channel_handler(callback_query: types.CallbackQuery):
        admin_id_raw = os.getenv("ADMIN_ID") or os.getenv("ADMIN_USER_ID")
        if not admin_id_raw or str(callback_query.from_user.id) != admin_id_raw:
            await callback_query.answer("Только администратор может отправлять новости", show_alert=True)
            return

        if not news_channel_id:
            await callback_query.answer("TELEGRAM_NEWS_CHAT_ID не задан", show_alert=True)
            return

        article_ref = callback_query.data.split(":", 1)[1]
        article_hash = repository.resolve_callback_article_hash(article_ref)
        if not article_hash:
            await callback_query.answer("Не нашёл новость для публикации", show_alert=True)
            return
        send_result = await news_pipeline.send_article_to_channel(article_hash)
        if send_result == "posted":
            await callback_query.answer("Опубликовано")
            if callback_query.message:
                try:
                    await callback_query.message.edit_reply_markup()
                except MessageNotModified:
                    pass
            return

        if send_result == "already_sent":
            await callback_query.answer("Эта новость уже была отправлена", show_alert=True)
            if callback_query.message:
                try:
                    await callback_query.message.edit_reply_markup()
                except MessageNotModified:
                    pass
            return

        error_messages = {
            "not_found": "Не нашёл сохранённую новость",
            "missing_text": "Нет сохранённого текста для публикации",
            "missing_image": "Нет сохранённой картинки для публикации",
        }
        await callback_query.answer(error_messages.get(send_result, f"Ошибка отправки: {send_result}"), show_alert=True)

    @dp.callback_query_handler(lambda call: (call.data or "").startswith("skip_news:"))
    async def skip_news_handler(callback_query: types.CallbackQuery):
        admin_id_raw = os.getenv("ADMIN_ID") or os.getenv("ADMIN_USER_ID")
        if not admin_id_raw or str(callback_query.from_user.id) != admin_id_raw:
            await callback_query.answer("Только администратор может пропускать новости", show_alert=True)
            return
        article_ref = callback_query.data.split(":", 1)[1]
        article_hash = repository.resolve_callback_article_hash(article_ref)
        if not article_hash:
            await callback_query.answer("Не нашёл новость для пропуска", show_alert=True)
            return
        result = await news_pipeline.skip_article_by_admin(article_hash)
        if result == "skipped":
            await callback_query.answer("Пропущено")
            if callback_query.message:
                try:
                    await callback_query.message.edit_reply_markup()
                except MessageNotModified:
                    pass
            return
        await callback_query.answer("Не удалось пропустить новость", show_alert=True)
