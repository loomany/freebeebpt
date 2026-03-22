from __future__ import annotations

import os
from typing import Any

from aiogram import Dispatcher, types

from services.gnews_service import TOPICS


def register_admin_news_handlers(dp: Dispatcher, *, context: dict[str, Any]) -> None:
    repository = context["repository"]
    gnews_service = context["gnews_service"]
    news_pipeline = context["news_pipeline"]
    news_post_mode = context["news_post_mode"]

    async def _ensure_admin(message: types.Message) -> bool:
        admin_id_raw = os.getenv("ADMIN_ID")
        if not admin_id_raw:
            await message.answer("ADMIN_ID не задан")
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
                    f"total_saved_articles: {stats['total_saved_articles']}",
                    f"total_posted_articles: {stats['total_posted_articles']}",
                ]
            )
        )

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

    @dp.message_handler(commands=["news_test"])
    async def news_test_handler(message: types.Message):
        if not await _ensure_admin(message):
            return
        await message.answer("Запускаю debug-пайплайн news_test...")
        summary = await news_pipeline.run_news_test()
        await message.answer(summary)
