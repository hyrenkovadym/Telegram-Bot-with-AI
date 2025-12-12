# bot_core/handlers/staff.py
from contextlib import suppress

from telegram import Update, ReplyKeyboardMarkup
from telegram.constants import ChatAction
from telegram.ext import ContextTypes

from ..config import (
    BACK_BTN,
    MODEL_STAFF,
    FREE_MODE,
    OPENAI_CLIENT,
)
from ..logging_setup import logger
from ..ui import bottom_keyboard
from ..utils import add_history, is_staff_phone
from ..gpt_helpers import build_messages_for_staff, clean_plain_text


# ----- клавіатура режиму співробітника -----
def staff_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [[BACK_BTN]],
        resize_keyboard=True,
        one_time_keyboard=False,
        selective=False,
    )


# ----- індикатор набору тексту тільки для цього модуля -----
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


# ----- вхід / вихід із режиму співробітника -----
async def on_staff_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Увімкнути режим співробітника.
    Доступ тільки для номерів, які проходять is_staff_phone().
    """
    user = update.effective_user
    phone = context.user_data.get("phone", "")

    if not phone or not is_staff_phone(phone):
        # Не даємо увімкнути staff-режим, якщо номер не зі списку співробітників
        await update.message.reply_text(
            "Режим співробітника доступний лише для співробітників FRENDT.",
            reply_markup=bottom_keyboard(
                context,
                tg_user_id=str(user.id),
            ),
        )
        return

    context.user_data["staff_mode"] = True
    await update.message.reply_text(
        "Режим співробітника увімкнено ✅\n"
        "Тепер ви можете ставити як робочі, так і особисті запитання.\n"
        "Ці повідомлення не потрапляють у лід-стрічку або Google Sheets.\n\n"
        "Щоб повернутися до звичайного режиму, натисніть «Назад».",
        reply_markup=staff_keyboard(),
    )


async def on_staff_back(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Вийти з режиму співробітника.
    """
    context.user_data["staff_mode"] = False
    await update.message.reply_text(
        "Повертаю вас у звичайний режим 👌",
        reply_markup=bottom_keyboard(
            context,
            tg_user_id=str(update.effective_user.id),
        ),
    )


# ----- основна відповідь у staff-режимі -----
async def answer_staff_mode(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    user_message: str,
):
    """
    Відповідь у режимі співробітника:
    - не пишемо в Google Sheets
    - не створюємо ліди
    - працюємо напряму з OpenAI
    """
    user = update.effective_user
    add_history(context, "user", user_message)

    # Якщо OpenAI недоступний
    if FREE_MODE or OPENAI_CLIENT is None:
        text = (
            "Режим співробітника працює тільки з активним підключенням до OpenAI. "
            "Зараз я можу лише зафіксувати ваш запит."
        )
        await update.message.reply_text(
            text,
            reply_markup=staff_keyboard(),
        )
        add_history(context, "assistant", text)
        return

    try:
        messages = build_messages_for_staff(context, user_message)

        kwargs = {
            "model": MODEL_STAFF,
            "messages": messages,
        }

        # GPT-5.* → max_completion_tokens, без temperature
        if MODEL_STAFF.startswith("gpt-5"):
            kwargs["max_completion_tokens"] = 900
        else:
            kwargs["max_tokens"] = 600
            kwargs["temperature"] = 0.3

        async with typing_during(update.effective_chat):
            response = OPENAI_CLIENT.chat.completions.create(**kwargs)

        gpt_text = clean_plain_text(
            response.choices[0].message.content or ""
        ).strip()

        await update.message.reply_text(
            gpt_text,
            reply_markup=staff_keyboard(),
        )
        add_history(context, "assistant", gpt_text)

    except Exception as e:
        logger.error("OpenAI staff mode error: %s", e)
        await update.message.reply_text(
            "Не вдалося отримати відповідь у режимі співробітника. "
            "Спробуйте ще раз пізніше.",
            reply_markup=staff_keyboard(),
        )
