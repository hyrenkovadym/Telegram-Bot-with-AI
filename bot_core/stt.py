# bot_core/stt.py
import os

from .config import OPENAI_CLIENT, FREE_MODE
from .logging_setup import logger

# Модель для розпізнавання голосу
# Можеш в .env задати OPENAI_TRANSCRIBE_MODEL=whisper-1
TRANSCRIBE_MODEL = os.getenv("OPENAI_TRANSCRIBE_MODEL", "whisper-1")


def transcribe_file(path: str) -> str | None:
    """
    Відправляє аудіофайл у OpenAI і повертає розпізнаний текст.
    Працює тільки якщо є OPENAI_API_KEY (FREE_MODE=False).
    """
    if FREE_MODE or OPENAI_CLIENT is None:
        logger.warning("[STT] FREE_MODE або немає OPENAI_CLIENT — транскрипція недоступна")
        return None

    try:
        with open(path, "rb") as f:
            resp = OPENAI_CLIENT.audio.transcriptions.create(
                model=TRANSCRIBE_MODEL,
                file=f,
                response_format="text",  # одразу текстом
            )

        # Якщо response_format="text", зазвичай це просто рядок
        if isinstance(resp, str):
            text = resp.strip()
        else:
            text = getattr(resp, "text", None)
            if not text and isinstance(resp, dict):
                text = resp.get("text")

        if not text:
            logger.warning("[STT] Порожня транскрипція")
            return None

        return text.strip()

    except Exception as e:
        logger.error("[STT] Помилка транскрипції: %s", e)
        return None
