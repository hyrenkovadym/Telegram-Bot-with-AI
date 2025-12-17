# bot_core/handlers/staff.py
import re
from contextlib import suppress

from telegram import Update, ReplyKeyboardMarkup
from telegram.constants import ChatAction
from telegram.ext import ContextTypes

from ..config import (
    BACK_BTN,
    MODEL_STAFF,
    MODEL_CHAT,
    FREE_MODE,
    OPENAI_CLIENT,
    ADMIN_IDS,
)
from ..logging_setup import logger
from ..ui import bottom_keyboard
from ..utils import add_history, is_staff_phone
from ..gpt_helpers import build_messages_for_staff, clean_plain_text


# Питання про "версію / модель / gpt"
_VERSION_Q_RE = re.compile(
    r"(яка|який)\s+(ти|в тебе)\s+верс(ія|iя)|"
    r"яка\s+верс(ія|iя)|"
    r"яка\s+модель|"
    r"який\s+ти\s+gpt|"
    r"\bgpt\b|"
    r"openai.*модель|"
    r"model\s*name|model\s*id",
    re.IGNORECASE,
)


def _version_reply() -> str:
    staff_model = (MODEL_STAFF or "").strip() or "невідомо"
    chat_model = (MODEL_CHAT or "").strip() or "невідомо"
    return (
        f"Зараз я працюю на моделі: {staff_model}.\n"
        f"У звичайному режимі бота використовується: {chat_model}.\n"
    )


def staff_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [[BACK_BTN]],
        resize_keyboard=True,
        one_time_keyboard=False,
        selective=False,
    )


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


async def on_staff_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Увімкнути режим співробітника.
    Доступ:
      - phone є в blacklist_phones.txt (is_staff_phone)
      - або tg_user_id є в ADMIN_IDS
    """
    user = update.effective_user
    phone = context.user_data.get("phone", "")

    is_admin = False
    try:
        is_admin = int(user.id) in (ADMIN_IDS or [])
    except Exception:
        is_admin = False

    if not is_admin and (not phone or not is_staff_phone(phone)):
        await update.message.reply_text(
            "Режим співробітника доступний лише для співробітників FRENDT.",
            reply_markup=bottom_keyboard(context, tg_user_id=str(user.id)),
        )
        return

    context.user_data["staff_mode"] = True
    await update.message.reply_text(
        "Режим співробітника увімкнено ✅\n"
        "Щоб повернутися до звичайного режиму, натисніть «Назад».",
        reply_markup=staff_keyboard(),
    )


async def on_staff_back(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    context.user_data["staff_mode"] = False
    await update.message.reply_text(
        "Ок, повернув у звичайний режим.",
        reply_markup=bottom_keyboard(context, tg_user_id=str(user.id)),
    )


async def answer_staff_mode(update: Update, context: ContextTypes.DEFAULT_TYPE, user_message: str):
    if FREE_MODE or OPENAI_CLIENT is None:
        await update.message.reply_text(
            "Staff-режим недоступний у FREE_MODE.",
            reply_markup=staff_keyboard(),
        )
        return

    # 1) Якщо питають про версію/модель — відповідаємо з ENV, не звертаємось до OpenAI
    if _VERSION_Q_RE.search(user_message or ""):
        text = _version_reply()
        add_history(context, "user", user_message)
        add_history(context, "assistant", text)
        await update.message.reply_text(text, reply_markup=staff_keyboard())
        return

    # 2) Звичайний staff-запит → OpenAI
    add_history(context, "user", user_message)

    messages = build_messages_for_staff(context, user_message)
    kwargs = {
        "model": MODEL_STAFF,
        "messages": messages,
    }

    chat = update.effective_chat
    async with typing_during(chat):
        try:
            resp = OPENAI_CLIENT.chat.completions.create(**kwargs)
            raw = (resp.choices[0].message.content or "") if resp and resp.choices else ""
        except Exception as e:
            logger.error("STAFF OpenAI error: %s", e)
            raw = ""

    text = clean_plain_text(raw).strip() or "Не отримав відповідь від моделі."
    add_history(context, "assistant", text)

    await update.message.reply_text(text, reply_markup=staff_keyboard())
