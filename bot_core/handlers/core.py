# bot_core/handlers/core.py
import asyncio
from contextlib import suppress

from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import ContextTypes

from ..config import (
    F_COMPANY,
    MODEL_CHAT,
    FREE_MODE,
    USE_WEB,
    OPENAI_CLIENT,
)
from ..drive_media import finalize_media_case  # закриття медіа-кейсу
from ..logging_setup import logger
from ..db import db_get_known_phone_by_tg, db_save_first_message
from ..gsheets import gsheet_append_row, gsheet_append_event
from ..ui import bottom_keyboard
from ..utils import (
    ensure_dialog,
    schedule_session_expiry,
    try_normalize_user_phone,
    touch_session,
    session_expired,
    reset_session,
    add_history,
    last_user_message,
    reload_blacklist,
    kb_retrieve_smart,
    pack_snippets,
    build_web_context,
    send_long_reply,
)
from ..gpt_helpers import build_messages_for_openai, clean_plain_text
from .contact import process_contact_submission
from .staff import answer_staff_mode


# ========= typing indicator =========
async def _typing_loop(chat):
    while True:
        with suppress(Exception):
            await chat.send_action(ChatAction.TYPING)
        await asyncio.sleep(4)


class typing_during:
    def __init__(self, chat):
        self.chat = chat
        self._task = None

    async def __aenter__(self):
        self._task = asyncio.create_task(_typing_loop(self.chat))
        with suppress(Exception):
            await self.chat.send_action(ChatAction.TYPING)
        return self

    async def __aexit__(self, exc_type, exc, tb):
        if self._task:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task


# ========= командні хендлери =========
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /start — скидаємо сесію, підтягуємо телефон, вітаємось, просимо номер.
    """
    reset_session(context)
    schedule_session_expiry(update, context)
    ensure_dialog(context)

    user = update.effective_user

    # спроба підвантажити телефон із постійної таблиці
    try:
        known = db_get_known_phone_by_tg(str(user.id))
    except Exception as e:
        logger.error("db_get_known_phone_by_tg error: %s", e)
        known = None

    if known:
        context.user_data["phone"] = known

    greeting = rf"Привіт, {user.mention_html()}! 👋 Я ваш ШІ-помічник {F_COMPANY}."

    if context.user_data.get("phone"):
        await update.message.reply_html(
            greeting,
            reply_markup=bottom_keyboard(context, tg_user_id=str(user.id)),
        )
    else:
        await update.message.reply_html(
            greeting
            + "\nЩоб ми могли з вами зв’язатися, поділіться, будь ласка, своїм номером телефону:",
            reply_markup=bottom_keyboard(context, tg_user_id=str(user.id)),
        )


async def cmd_reload_blacklist(update: Update, context: ContextTypes.DEFAULT_TYPE):
    count = reload_blacklist()
    await update.message.reply_text(
        f"Готово. Оновлено чорний список/список спец-номерів: {count} номерів.",
        reply_markup=bottom_keyboard(
            context,
            tg_user_id=str(update.effective_user.id),
        ),
    )


async def cmd_last(update: Update, context: ContextTypes.DEFAULT_TYPE):
    prev_user_msg = last_user_message(context)
    if prev_user_msg:
        await update.message.reply_text(
            "Останнє ваше повідомлення:\n\n" + prev_user_msg,
            reply_markup=bottom_keyboard(
                context,
                tg_user_id=str(update.effective_user.id),
            ),
        )
    else:
        await update.message.reply_text(
            "Поки що немає попереднього повідомлення у моїй історії.",
            reply_markup=bottom_keyboard(
                context,
                tg_user_id=str(update.effective_user.id),
            ),
        )


async def cmd_model(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"Поточна модель GPT: {MODEL_CHAT}")


async def cmd_reload_kb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Просто імпортуємо utils та перебудовуємо
    from .. import utils as utils_mod

    idx = utils_mod.kb_build_or_load()
    utils_mod._KB_INDEX = idx
    await update.message.reply_text(
        f"Базу знань оновлено. Фрагментів: {len(idx.get('chunks', []))}.",
        reply_markup=bottom_keyboard(
            context,
            tg_user_id=str(update.effective_user.id),
        ),
    )


# ========= інші хендлери =========
async def block_non_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Будь-які не-текстові повідомлення (крім контакту, голосових та фото,
    для яких є окремі хендлери).
    """
    schedule_session_expiry(update, context)
    await update.message.reply_text(
        "Будь ласка, надсилайте текстове повідомлення 💬.",
        reply_markup=bottom_keyboard(
            context,
            tg_user_id=str(update.effective_user.id),
        ),
    )


async def on_manager_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Кнопка "Зв’язатись з менеджером".
    Створюємо подію в БД та Google Sheets.
    """
    schedule_session_expiry(update, context)
    ensure_dialog(context)

    user = update.effective_user

    # підтягуємо телефон із БД, якщо його немає в user_data
    if not context.user_data.get("phone"):
        try:
            known = db_get_known_phone_by_tg(str(user.id))
        except Exception:
            known = None
        if known:
            context.user_data["phone"] = known

    if not context.user_data.get("phone"):
        await update.message.reply_text(
            "Щоб менеджер зміг з вами зв’язатися, будь ласка, спочатку поділіться своїм номером телефону.",
            reply_markup=bottom_keyboard(
                context,
                tg_user_id=str(update.effective_user.id),
            ),
        )
        return

    full_name = ((user.first_name or "") + " " + (user.last_name or "")).strip()
    phone = context.user_data.get("phone", "")

    # запис у lead_messages
    try:
        db_save_first_message(
            phone=phone,
            full_name=full_name,
            text="Заявка: зв’язок з менеджером",
            tg_user_id=str(user.id),
        )
    except Exception as e:
        logger.error("DB save manager request error: %s", e)

    # лог у Google Sheets
    try:
        gsheet_append_event(
            "Заявка: зв’язок з менеджером",
            full_name=full_name,
            phone=phone,
        )
    except Exception as e:
        logger.error("[GSHEET] event insert error: %s", e)

    await update.message.reply_text(
        "Передав менеджеру вашу заявку. Очікуйте на дзвінок або відповідь найближчим часом.",
        reply_markup=bottom_keyboard(
            context,
            tg_user_id=str(update.effective_user.id),
        ),
    )


async def _answer_free_mode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Простий шаблон, якщо OpenAI не налаштований (FREE_MODE).
    """
    text = (
        "Дякую! Запит прийнято. Менеджер зв'яжеться з вами найближчим часом.\n\n🔧 FRENDT."
    )
    await send_long_reply(
        update,
        context,
        text,
        reply_markup=bottom_keyboard(
            context,
            tg_user_id=str(update.effective_user.id),
        ),
    )
    add_history(context, "assistant", text)


# ========= головний message-handler =========
async def handle_message(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    text_override: str | None = None,
):
    """
    Головний обробник повідомлень:
    - працює як для звичайного тексту,
    - так і для голосових (через text_override з handlers/voice.py).
    """
    schedule_session_expiry(update, context)
    ensure_dialog(context)

    # 1) Беремо текст:
    if text_override is not None:
        raw_text = text_override or ""
    else:
        if not update.message or not (update.message.text or "").strip():
            await update.message.reply_text(
                "Будь ласка, надсилайте текстовий запит (або чіткіше голосове) 💬.",
                reply_markup=bottom_keyboard(
                    context,
                    tg_user_id=str(update.effective_user.id),
                ),
            )
            return
        raw_text = update.message.text or ""

    user_message = raw_text.strip()
    if not user_message:
        await update.message.reply_text(
            "Будь ласка, надсилайте текстовий запит (або чіткіше голосове) 💬.",
            reply_markup=bottom_keyboard(
                context,
                tg_user_id=str(update.effective_user.id),
            ),
        )
        return

    user = update.effective_user
    touch_session(context)
    lm = user_message.lower()

    # ------- сервіс/кабелі: коментар + завершення медіа-кейсу -------
    flow = context.user_data.get("flow")
    if flow in ("service", "cable"):
        media_case = context.user_data.get("media_case")
        normalized = lm.strip()

        done_variants = {
            "готово",
            "готово.",
            "це все",
            "це все.",
            "все",
            "все.",
        }

        # Якщо є активний кейс і юзер пише "готово" → закриваємо кейс
        if media_case and normalized in done_variants:
            comment_text = (context.user_data.get("media_comment") or "").strip()
            await finalize_media_case(update, context, comment_text=comment_text)
            context.user_data.pop("media_comment", None)
            return

        # Інакше сприймаємо текст як опис проблеми/коментар до кейсу
        if normalized not in done_variants:
            prev = context.user_data.get("media_comment") or ""
            if prev:
                context.user_data["media_comment"] = prev + "\n" + user_message
            else:
                context.user_data["media_comment"] = user_message

    # підтягуємо телефон із «постійної» таблиці, якщо ще не в user_data
    if not context.user_data.get("phone"):
        try:
            known = db_get_known_phone_by_tg(str(user.id))
        except Exception:
            known = None
        if known:
            context.user_data["phone"] = known

    # тайм-аут сесії
    if session_expired(context):
        reset_session(context)
        await update.message.reply_text(
            "⏳ Сесію завершено через 1 годину неактивності.\n"
            f"Я ваш помічник {F_COMPANY}. Поділіться номером телефону, будь ласка:",
            reply_markup=bottom_keyboard(
                context,
                tg_user_id=str(user.id),
            ),
        )
        return

    # ===== STAFF MODE =====
    if context.user_data.get("staff_mode"):
        await answer_staff_mode(update, context, user_message)
        return

    # ===== Спец-запит: "покажи попереднє" =====
    if any(
        kw in lm
        for kw in [
            "перешли мені",
            "перешли",
            "скинь поперед",
            "попереднє повідомлення",
            "що я надіслав перед цим",
            "що я відправив перед цим",
            "останнє моє повідомлення",
        ]
    ):
        prev_user_msg = last_user_message(context)
        if prev_user_msg:
            await update.message.reply_text(
                "Ось ваше попереднє повідомлення:\n\n" + prev_user_msg,
                reply_markup=bottom_keyboard(
                    context,
                    tg_user_id=str(update.effective_user.id),
                ),
            )
        else:
            await update.message.reply_text(
                "Не бачу попереднього повідомлення в історії (можливо, це перший меседж або сесію скинуто).",
                reply_markup=bottom_keyboard(
                    context,
                    tg_user_id=str(update.effective_user.id),
                ),
            )
        return

    # Якщо ще немає телефону — просимо
    if not context.user_data.get("phone"):
        maybe_phone = try_normalize_user_phone(user_message)
        if maybe_phone:
            await process_contact_submission(update, context, maybe_phone)
            return
        await update.message.reply_text(
            "Щоб я міг допомогти швидше, будь ласка, поділіться номером телефону:",
            reply_markup=bottom_keyboard(
                context,
                tg_user_id=str(update.effective_user.id),
            ),
        )
        return

    # Перше повідомлення → лід-стрічка
    if not context.user_data.get("first_q_saved"):
        try:
            full_name = ((user.first_name or "") + " " + (user.last_name or "")).strip()
            db_save_first_message(
                phone=context.user_data.get("phone", ""),
                full_name=full_name,
                text=user_message,
                tg_user_id=str(user.id),
            )
            context.user_data["first_q_saved"] = True
        except Exception as e:
            logger.error("DB save first message error: %s", e)

    # Лог у Google Sheets
    try:
        full_name = ((user.first_name or "") + " " + (user.last_name or "")).strip()
        gsheet_append_row(
            full_name=full_name,
            phone=context.user_data.get("phone", ""),
            message=user_message,
        )
    except Exception as e:
        logger.error("[GSHEET] append per-message error: %s", e)

    add_history(context, "user", user_message)

    # FREE_MODE → шаблон
    if FREE_MODE or OPENAI_CLIENT is None:
        await _answer_free_mode(update, context)
        return

    # 1) KB
    kb_hits = kb_retrieve_smart(user_message, k=6)
    if kb_hits:
        kb_context = pack_snippets(kb_hits)
        try:
            messages = build_messages_for_openai(
                context,
                source_mode="kb",
                last_user_text=user_message,
                kb_context=kb_context,
            )

            kwargs = {
                "model": MODEL_CHAT,
                "messages": messages,
            }

            if MODEL_CHAT.startswith("gpt-5"):
                kwargs["max_completion_tokens"] = 1200
            else:
                kwargs["max_tokens"] = 1200
                kwargs["temperature"] = 0.2

            async with typing_during(update.effective_chat):
                response = OPENAI_CLIENT.chat.completions.create(**kwargs)

            logger.info("OpenAI KB model used: %s", response.model)
            raw = response.choices[0].message.content or ""
            logger.info("OpenAI RAW answer: %r", raw)

            gpt_text = clean_plain_text(raw).strip()
            if not gpt_text:
                gpt_text = (
                    "Вибачте, я не отримав зрозумілої текстової відповіді від моделі. "
                    "Спробуйте, будь ласка, переформулювати запит простішими словами."
                )

            await send_long_reply(
                update,
                context,
                gpt_text + "\n\n🔧 FRENDT.",
                reply_markup=bottom_keyboard(
                    context,
                    tg_user_id=str(update.effective_user.id),
                ),
            )

            add_history(context, "assistant", gpt_text)
            return
        except Exception as e:
            logger.error("OpenAI KB mode error: %s", e)

    # 2) Web fallback
    if USE_WEB:
        try:
            web_ctx = build_web_context(user_message)
            messages = build_messages_for_openai(
                context,
                source_mode="web",
                last_user_text=user_message,
                web_context=web_ctx,
            )

            kwargs = {
                "model": MODEL_CHAT,
                "messages": messages,
            }

            if MODEL_CHAT.startswith("gpt-5"):
                kwargs["max_completion_tokens"] = 900
            else:
                kwargs["max_tokens"] = 900
                kwargs["temperature"] = 0.3

            async with typing_during(update.effective_chat):
                response = OPENAI_CLIENT.chat.completions.create(**kwargs)

            logger.info("OpenAI WEB model used: %s", response.model)
            raw = response.choices[0].message.content or ""
            logger.info("OpenAI RAW answer: %r", raw)

            gpt_text = clean_plain_text(raw).strip()
            if not gpt_text:
                gpt_text = (
                    "Вибачте, я не отримав зрозумілої текстової відповіді від моделі. "
                    "Спробуйте, будь ласка, переформулювати запит простішими словами."
                )

            await send_long_reply(
                update,
                context,
                gpt_text + "\n\n🔧 FRENDT.",
                reply_markup=bottom_keyboard(
                    context,
                    tg_user_id=str(update.effective_user.id),
                ),
            )

            add_history(context, "assistant", gpt_text)
            return
        except Exception as e:
            logger.error("Web fallback error: %s", e)

    # 3) Plain
    try:
        messages = build_messages_for_openai(
            context,
            source_mode="plain",
            last_user_text=user_message,
        )

        kwargs = {
            "model": MODEL_CHAT,
            "messages": messages,
        }

        if MODEL_CHAT.startswith("gpt-5"):
            kwargs["max_completion_tokens"] = 900
        else:
            kwargs["max_tokens"] = 900
            kwargs["temperature"] = 0.3

        async with typing_during(update.effective_chat):
            response = OPENAI_CLIENT.chat.completions.create(**kwargs)

        logger.info("OpenAI PLAIN model used: %s", response.model)
        raw = response.choices[0].message.content or ""
        logger.info("OpenAI RAW answer: %r", raw)

        gpt_text = clean_plain_text(raw).strip()
        if not gpt_text:
            gpt_text = (
                "Вибачте, я не отримав зрозумілої текстової відповіді від моделі. "
                "Спробуйте, будь ласка, переформулювати запит простішими словами."
            )

        await send_long_reply(
            update,
            context,
            gpt_text + "\n\n🔧 FRENDT.",
            reply_markup=bottom_keyboard(
                context,
                tg_user_id=str(update.effective_user.id),
            ),
        )

        add_history(context, "assistant", gpt_text)
    except Exception as e:
        logger.error("OpenAI plain mode error: %s", e)
        await update.message.reply_text(
            "Тимчасово не можу отримати відповідь. Спробуйте повторити запит або поставити його простіше.",
            reply_markup=bottom_keyboard(
                context,
                tg_user_id=str(update.effective_user.id),
            ),
        )
