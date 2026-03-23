from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

try:
    import fal_client
except ImportError:  # pragma: no cover
    fal_client = None


class FalImageService:
    def __init__(self) -> None:
        self.enabled = os.getenv("FAL_KEY", "").strip() != ""
        self.model = os.getenv("FAL_MODEL", "fal-ai/nano-banana-2")
        self.aspect_ratio = os.getenv("FAL_IMAGE_ASPECT_RATIO", "9:16")
        self.num_images = int(os.getenv("FAL_IMAGE_NUM", "1"))
        self.output_format = os.getenv("FAL_IMAGE_OUTPUT_FORMAT", "png")
        self.resolution = os.getenv("FAL_IMAGE_RESOLUTION", "2K")
        self.safety_tolerance = int(os.getenv("FAL_SAFETY_TOLERANCE", "4"))
        self.timeout_seconds = int(os.getenv("FAL_TIMEOUT_SECONDS", "90"))
        self.max_retries = 1
        if self.enabled:
            os.environ.setdefault("FAL_KEY", os.getenv("FAL_KEY", ""))

    async def _submit(self, prompt: str) -> dict[str, Any]:
        if fal_client is None:
            raise RuntimeError("fal_client is not installed")
        return await asyncio.wait_for(
            fal_client.subscribe_async(
                self.model,
                arguments={
                    "prompt": prompt,
                    "num_images": self.num_images,
                    "aspect_ratio": self.aspect_ratio,
                    "output_format": self.output_format,
                    "resolution": self.resolution,
                    "safety_tolerance": self.safety_tolerance,
                },
            ),
            timeout=self.timeout_seconds,
        )

    async def generate_news_image(self, prompt: str) -> str | None:
        if not prompt.strip():
            return None
        if not self.enabled:
            logger.warning("[FAL] skipped because FAL_KEY is missing")
            return None
        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                logger.info("[FAL] submit model=%s", self.model)
                response = await self._submit(prompt)
                images = response.get("images") or []
                image_url = images[0].get("url") if images else None
                if image_url:
                    logger.info("[FAL] success image_url=%s", image_url)
                    return image_url
                raise ValueError("response does not contain images[0].url")
            except Exception as error:  # noqa: BLE001
                last_error = error
                logger.warning("[FAL] failed attempt=%s reason=%s", attempt + 1, error)
        logger.error("[FAL] failed reason=%s", last_error)
        return None
