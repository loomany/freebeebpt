from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from openai import AsyncOpenAI

logger = logging.getLogger(__name__)


class KazakhTranslator:
    def __init__(self, client: "AsyncOpenAI" | None):
        self.client = client
        self.enabled = os.getenv("TRANSLATE_ENABLED", "true").lower() == "true"
        self.target_lang = os.getenv("TRANSLATE_TARGET_LANG", "kk")
        self.chunk_size = max(500, int(os.getenv("TRANSLATION_CHUNK_SIZE", "3000")))
        self.model = os.getenv("TRANSLATION_MODEL", "gpt-4.1-mini")

    def _chunk_text(self, text: str) -> list[str]:
        if len(text) <= self.chunk_size:
            return [text]
        chunks: list[str] = []
        current = ""
        for paragraph in text.split("\n\n"):
            candidate = f"{current}\n\n{paragraph}".strip() if current else paragraph
            if len(candidate) <= self.chunk_size:
                current = candidate
                continue
            if current:
                chunks.append(current)
            current = paragraph
        if current:
            chunks.append(current)
        return chunks

    async def translate_to_kazakh(self, text: str) -> str:
        if not text.strip() or not self.enabled or not self.client:
            return text

        translated_parts: list[str] = []
        for chunk in self._chunk_text(text):
            try:
                response = await self.client.responses.create(
                    model=self.model,
                    input=[
                        {
                            "role": "system",
                            "content": [
                                {
                                    "type": "text",
                                    "text": (
                                        "Переведи новостную статью с английского на казахский язык. "
                                        "Сохрани нейтральный новостной стиль. Не сокращай текст. "
                                        "Имена, названия команд, турниров, СМИ и брендов оставляй корректно. "
                                        "Не добавляй ничего от себя."
                                    ),
                                }
                            ],
                        },
                        {
                            "role": "user",
                            "content": [{"type": "text", "text": chunk}],
                        },
                    ],
                )
                translated_parts.append(response.output_text.strip())
            except Exception as error:  # noqa: BLE001
                logger.warning("[TRANSLATION] failed, sending original chunk: %s", error)
                translated_parts.append(chunk)
        return "\n\n".join(part for part in translated_parts if part).strip() or text
