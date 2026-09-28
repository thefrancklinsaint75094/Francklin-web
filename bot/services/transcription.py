"""Transcription des vocaux via Whisper (optionnel)."""
from __future__ import annotations

import asyncio
import logging

log = logging.getLogger(__name__)

TIMEOUT = 20.0
RETRY_DELAY = 2.0


class TranscriptionUnavailable(Exception):
    pass


async def transcribe(audio: bytes, api_key: str, filename: str = "voice.ogg") -> str:
    from openai import AsyncOpenAI

    client = AsyncOpenAI(api_key=api_key, timeout=TIMEOUT, max_retries=0)
    last_exc: Exception | None = None
    try:
        for attempt in range(2):
            try:
                result = await client.audio.transcriptions.create(
                    model="whisper-1",
                    file=(filename, audio),
                    language="fr",
                    response_format="text",
                )
                text = result if isinstance(result, str) else getattr(result, "text", "")
                return (text or "").strip()
            except Exception as exc:  # noqa: BLE001 — toute erreur Whisper = vocal non lu
                last_exc = exc
                log.warning("Whisper tentative %d échouée : %s", attempt + 1, exc)
                if attempt == 0:
                    await asyncio.sleep(RETRY_DELAY)
    finally:
        await client.close()
    raise TranscriptionUnavailable(str(last_exc))
