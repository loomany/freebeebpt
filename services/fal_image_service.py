from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

try:
    import httpx
except ImportError:  # pragma: no cover
    httpx = None

try:
    import fal_client
except ImportError:  # pragma: no cover
    fal_client = None


@dataclass(slots=True)
class FalGenerationResult:
    request_id: str | None
    status: str
    image_url: str | None = None
    error: str | None = None
    attempts: int = 0
    payload: dict[str, Any] | None = None


class FalImageService:
    def __init__(self) -> None:
        self.enabled = os.getenv("FAL_KEY", "").strip() != ""
        self.model = os.getenv("FAL_MODEL", "fal-ai/nano-banana-2")
        self.aspect_ratio = os.getenv("FAL_IMAGE_ASPECT_RATIO", "9:16")
        self.num_images = int(os.getenv("FAL_IMAGE_NUM", "1"))
        self.output_format = os.getenv("FAL_IMAGE_OUTPUT_FORMAT", "png")
        self.resolution = os.getenv("FAL_IMAGE_RESOLUTION", "2K")
        self.safety_tolerance = int(os.getenv("FAL_SAFETY_TOLERANCE", "4"))
        self.timeout_seconds = int(os.getenv("FAL_TIMEOUT_SECONDS", "120"))
        self.max_retries = int(os.getenv("FAL_MAX_RETRIES", "1"))
        self.poll_interval_seconds = float(os.getenv("FAL_POLL_INTERVAL_SECONDS", "2.0"))
        self.queue_base_url = os.getenv("FAL_QUEUE_BASE_URL", "https://queue.fal.run").rstrip("/")
        self.http_timeout_seconds = float(os.getenv("FAL_HTTP_TIMEOUT_SECONDS", "30"))
        max_attempts_env = os.getenv("FAL_MAX_POLL_ATTEMPTS", "").strip()
        computed_attempts = max(1, int(self.timeout_seconds / self.poll_interval_seconds))
        self.max_poll_attempts = int(max_attempts_env) if max_attempts_env else computed_attempts
        self.terminal_statuses = {"COMPLETED", "FAILED", "CANCELLED"}
        if self.enabled:
            os.environ.setdefault("FAL_KEY", os.getenv("FAL_KEY", ""))

    def _headers(self) -> dict[str, str]:
        key = os.getenv("FAL_KEY", "").strip()
        return {
            "Authorization": f"Key {key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def _arguments(self, prompt: str) -> dict[str, Any]:
        return {
            "prompt": prompt,
            "num_images": self.num_images,
            "aspect_ratio": self.aspect_ratio,
            "output_format": self.output_format,
            "resolution": self.resolution,
            "safety_tolerance": self.safety_tolerance,
        }

    def _extract_image_url(self, payload: dict[str, Any] | None) -> str | None:
        if not isinstance(payload, dict):
            return None
        result_data = payload.get("data")
        if isinstance(result_data, dict):
            payload = result_data
        images = payload.get("images") or []
        if not images or not isinstance(images, list):
            return None
        first = images[0] or {}
        if not isinstance(first, dict):
            return None
        return first.get("url")

    def _normalize_status(self, status: Any, default: str = "UNKNOWN") -> str:
        if status is None:
            return default
        if isinstance(status, dict):
            value = status.get("status", default)
        else:
            value = getattr(status, "status", status)
        if isinstance(value, str):
            return value.upper()
        return str(value).upper()

    def _extract_error(self, payload: Any) -> str | None:
        if payload is None:
            return None
        if isinstance(payload, Exception):
            return str(payload)
        if isinstance(payload, dict):
            for key in ("error", "detail", "message"):
                value = payload.get(key)
                if value:
                    return str(value)
        detail = getattr(payload, "error", None) or getattr(payload, "detail", None) or getattr(payload, "message", None)
        return str(detail) if detail else None

    async def _submit_via_sdk(self, prompt: str):
        return await fal_client.submit_async(
            self.model,
            arguments=self._arguments(prompt),
        )

    async def _submit_via_http(self, prompt: str) -> tuple[str | None, dict[str, Any]]:
        if httpx is None:
            raise RuntimeError("httpx is not installed")
        async with httpx.AsyncClient(timeout=self.http_timeout_seconds) as client:
            response = await client.post(
                f"{self.queue_base_url}/{self.model}",
                headers=self._headers(),
                json=self._arguments(prompt),
            )
            response.raise_for_status()
            payload = response.json()
        return payload.get("request_id"), payload

    async def _submit(self, prompt: str) -> tuple[Any, str | None]:
        if fal_client is not None:
            handle = await self._submit_via_sdk(prompt)
            return handle, getattr(handle, "request_id", None)
        request_id, payload = await self._submit_via_http(prompt)
        return payload, request_id

    async def _poll_status_with_sdk(self, handle: Any):
        return await handle.status(with_logs=True)

    async def _poll_status_with_http(self, request_id: str) -> dict[str, Any]:
        if httpx is None:
            raise RuntimeError("httpx is not installed")
        async with httpx.AsyncClient(timeout=self.http_timeout_seconds) as client:
            response = await client.get(
                f"{self.queue_base_url}/{self.model}/requests/{request_id}/status",
                headers=self._headers(),
                params={"logs": "true"},
            )
            if response.status_code not in {200, 202}:
                response.raise_for_status()
            return response.json()

    async def _poll_status(self, handle_or_payload: Any, request_id: str | None):
        if fal_client is not None and hasattr(handle_or_payload, "status"):
            return await self._poll_status_with_sdk(handle_or_payload)
        if not request_id:
            raise RuntimeError("missing request_id for HTTP polling")
        return await self._poll_status_with_http(request_id)

    async def _result_with_sdk(self, handle: Any):
        return await handle.get()

    async def _result_with_http(self, request_id: str) -> dict[str, Any]:
        if httpx is None:
            raise RuntimeError("httpx is not installed")
        async with httpx.AsyncClient(timeout=self.http_timeout_seconds) as client:
            response = await client.get(
                f"{self.queue_base_url}/{self.model}/requests/{request_id}/result",
                headers=self._headers(),
            )
            response.raise_for_status()
            return response.json()

    async def _get_result(self, handle_or_payload: Any, request_id: str | None) -> dict[str, Any]:
        if fal_client is not None and hasattr(handle_or_payload, "get"):
            return await self._result_with_sdk(handle_or_payload)
        if not request_id:
            raise RuntimeError("missing request_id for result fetch")
        return await self._result_with_http(request_id)

    async def generate_news_image(self, prompt: str) -> FalGenerationResult:
        if not prompt.strip():
            return FalGenerationResult(request_id=None, status="skipped", error="image_prompt_en is empty")
        if not self.enabled:
            logger.warning("[FAL] skipped because FAL_KEY is missing")
            return FalGenerationResult(request_id=None, status="skipped", error="FAL_KEY is missing")

        last_error: Exception | None = None
        for retry in range(self.max_retries + 1):
            request_id: str | None = None
            try:
                logger.info("[FAL] submit model=%s prompt=%s", self.model, prompt[:200])
                handle_or_payload, request_id = await self._submit(prompt)
                logger.info("[FAL] submit ok request_id=%s model=%s", request_id, self.model)

                terminal_state: str | None = None
                for attempt in range(1, self.max_poll_attempts + 1):
                    if terminal_state == "COMPLETED":
                        logger.warning("[FAL] completed repeated after terminal state request_id=%s", request_id)
                        break
                    status_payload = await asyncio.wait_for(
                        self._poll_status(handle_or_payload, request_id),
                        timeout=self.http_timeout_seconds,
                    )
                    status = self._normalize_status(status_payload, default="IN_PROGRESS")
                    logs = getattr(status_payload, "logs", None)
                    if isinstance(status_payload, dict):
                        logs = status_payload.get("logs", logs)
                    queue_position = getattr(status_payload, "queue_position", None)
                    if isinstance(status_payload, dict):
                        queue_position = status_payload.get("queue_position", queue_position)
                    logger.info(
                        "[FAL] polling attempt=%s/%s request_id=%s status=%s queue_position=%s logs=%s",
                        attempt,
                        self.max_poll_attempts,
                        request_id,
                        status,
                        queue_position,
                        len(logs or []),
                    )
                    if status == "COMPLETED":
                        terminal_state = status
                        logger.info("[FAL] completed request_id=%s", request_id)
                        try:
                            result_payload = await asyncio.wait_for(
                                self._get_result(handle_or_payload, request_id),
                                timeout=self.http_timeout_seconds,
                            )
                        except Exception as result_error:  # noqa: BLE001
                            logger.error("[FAL] result fetch failed request_id=%s error=%s", request_id, result_error)
                            return FalGenerationResult(
                                request_id=request_id,
                                status="failed",
                                error=str(result_error),
                                attempts=attempt,
                            )
                        logger.info("[FAL] result payload = %s", result_payload)
                        image_url = self._extract_image_url(result_payload)
                        logger.info("[FAL] image_url parsed=%s request_id=%s", image_url, request_id)
                        if image_url:
                            return FalGenerationResult(
                                request_id=request_id,
                                status="success",
                                image_url=image_url,
                                attempts=attempt,
                                payload=result_payload,
                            )
                        error_message = "response does not contain data.images[0].url"
                        logger.error("[FAL] completed but empty result request_id=%s error=%s payload=%s", request_id, error_message, result_payload)
                        return FalGenerationResult(
                            request_id=request_id,
                            status="failed",
                            error=error_message,
                            attempts=attempt,
                            payload=result_payload,
                        )
                    if status in {"FAILED", "CANCELLED"}:
                        error_message = self._extract_error(status_payload) or f"terminal status {status}"
                        logger.error("[FAL] failed error=%s request_id=%s", error_message, request_id)
                        return FalGenerationResult(
                            request_id=request_id,
                            status=status.lower(),
                            error=error_message,
                            attempts=attempt,
                            payload=status_payload if isinstance(status_payload, dict) else None,
                        )
                    await asyncio.sleep(self.poll_interval_seconds)

                timeout_error = (
                    f"timed out after {self.timeout_seconds}s "
                    f"({self.max_poll_attempts} attempts x {self.poll_interval_seconds:.1f}s poll interval)"
                )
                if request_id:
                    try:
                        logger.warning("[FAL] timeout reached; checking final result request_id=%s", request_id)
                        result_payload = await asyncio.wait_for(
                            self._get_result(handle_or_payload, request_id),
                            timeout=self.http_timeout_seconds,
                        )
                        logger.info("[FAL] late result payload = %s", result_payload)
                        image_url = self._extract_image_url(result_payload)
                        if image_url:
                            logger.info("[FAL] recovered completed result after timeout request_id=%s", request_id)
                            return FalGenerationResult(
                                request_id=request_id,
                                status="success",
                                image_url=image_url,
                                attempts=self.max_poll_attempts,
                                payload=result_payload,
                            )
                    except Exception as late_result_error:  # noqa: BLE001
                        logger.warning(
                            "[FAL] late result fetch failed after timeout request_id=%s error=%s",
                            request_id,
                            late_result_error,
                        )
                logger.error("[FAL] timeout request_id=%s error=%s", request_id, timeout_error)
                return FalGenerationResult(
                    request_id=request_id,
                    status="timeout",
                    error=timeout_error,
                    attempts=self.max_poll_attempts,
                )
            except Exception as error:  # noqa: BLE001
                last_error = error
                logger.warning("[FAL] failed attempt=%s/%s request_id=%s reason=%s", retry + 1, self.max_retries + 1, request_id, error)
        logger.error("[FAL] failed reason=%s", last_error)
        return FalGenerationResult(request_id=None, status="failed", error=str(last_error) if last_error else "unknown error")
