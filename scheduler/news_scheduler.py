from __future__ import annotations

import asyncio
import logging
import os

logger = logging.getLogger(__name__)


async def run_hourly_news_scheduler(news_pipeline) -> None:
    poll_minutes = max(1, int(os.getenv("NEWS_POLL_MINUTES", "15")))
    while True:
        try:
            await news_pipeline.run_fetch_cycle(trigger="scheduler")
        except Exception:  # noqa: BLE001
            logger.exception("[NEWS SCHEDULER] cycle failed")
        await asyncio.sleep(60 * poll_minutes)
