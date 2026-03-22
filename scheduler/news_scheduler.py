from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger(__name__)


async def run_hourly_news_scheduler(news_pipeline) -> None:
    while True:
        try:
            await news_pipeline.run_fetch_cycle(trigger="scheduler")
        except Exception:  # noqa: BLE001
            logger.exception("[NEWS SCHEDULER] hourly cycle failed")
        await asyncio.sleep(60 * 60)
