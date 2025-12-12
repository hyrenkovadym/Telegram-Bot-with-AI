# bot_core/handlers/voice.py
import os
import io
import tempfile
from contextlib import suppress

from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import ContextTypes

from ..config import FREE_MODE, OPENAI_CLIENT
from ..logging_setup import logger
from ..utils import ensure_dialog, schedule_session_expiry, touch_session
from .core import handle_message


async def _typing_loop(chat):
    import asyncio
    while True:
        with suppress(Exception):
            await chat.send_action(ChatAction.TYPING)
        await asyncio.sleep(4)


class typing_during:
    def __init__(self, chat):
        self.chat = chat
        self._task = None

    async def __aenter__(self):
        import asyncio
        self._task = asyncio.create_task(_typing_loop(self.chat))
        with suppress(Exception):
            await self.chat.send_action(ChatAction.TYPING)
        return self

    async def __aexit__(self, exc_type, exc, tb):
        import asyncio
        if self._task:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task


async def on_voice_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    1) качаємо голосове
    2) віддаємо в OpenAI (whisper-1) БЕЗ примусу мови
    3) показуємо, що розпізнали
    4) кидаємо далі в handle_message як текст
    """
    schedule_session_expiry(update, context)
    ensure_dialog(context)
    touch_session(context)

    if not update.message:
        return

    voice_or_audio = update.message.voice or update.message.audio
    if not voice_or_audio:
        return

    if FREE_MODE or OPENAI_CLIENT is None:
        await update.message.reply_text(
            "Зараз голосові повідомлення недоступні. Надішліть, будь ласка, текстом."
        )
        return

    # 1) качаємо у тимчасовий файл
    tg_file = await voice_or_audio.get_file()
    with tempfile.NamedTemporaryFile(suffix=".oga", delete=False) as tmp:
        tmp_path = tmp.name
        await tg_file.download_to_drive(tmp_path)

    try:
        with open(tmp_path, "rb") as f:
            data = f.read()
        audio_buf = io.BytesIO(data)
        audio_buf.name = "audio.oga"

        # 2) розпізнаємо без явного language — хай сама вирішує
        resp = OPENAI_CLIENT.audio.transcriptions.create(
            model="whisper-1",
            file=audio_buf,
        )
        text = (getattr(resp, "text", "") or "").strip()
    except Exception as e:
        logger.error("Voice STT error: %s", e)
        await update.message.reply_text(
            "Не вдалося розпізнати голос. Спробуйте, будь ласка, ще раз або надішліть текстом."
        )
        return
    finally:
        with suppress(Exception):
            os.remove(tmp_path)

    if not text:
        await update.message.reply_text(
            "Я не почув тексту у цьому голосовому. Спробуйте ще раз, будь ласка."
        )
        return

    # 3) показуємо, що розпізнали
    #await update.message.reply_text(f"Я розпізнав ваш голос як:\n\n«{text}»")

    # 4) і тепер йдемо в той самий пайплайн, що й текст
    async with typing_during(update.effective_chat):
        await handle_message(update, context, text_override=text)
