# bot_core/handlers/core.py
import asyncio
from contextlib import suppress

from telegram import Update, Message
from telegram.ext import ContextTypes
from telegram.constants import ChatAction

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
from ..gpt_helpers import (
    build_messages_for_openai,
    openai_chat_with_retry,
)
from .contact import process_contact_submission
from .staff import answer_staff_mode


# ========= командні хендлери =========
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /start — скидаємо сесію, вітаємось.
    Без обов'язкового запиту номера телефону.
    """
    reset_session(context)
    schedule_session_expiry(update, context)
    ensure_dialog(context)

    user = update.effective_user

    # опціонально: тихо підтягуємо телефон з БД, щоб не втратити старі ліди
    try:
        known = db_get_known_phone_by_tg(str(user.id))
    except Exception as e:
        logger.error("db_get_known_phone_by_tg error: %s", e)
        known = None

    if known:
        context.user_data["phone"] = known

    greeting = rf"Привіт, {user.mention_html()}! 👋 Я ваш ШІ-помічник {F_COMPANY}."

    await update.message.reply_html(
        greeting,
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

    # підтягуємо телефон із БД, якщо його немає в user_data (тихо)
    if not context.user_data.get("phone"):
        try:
            known = db_get_known_phone_by_tg(str(user.id))
        except Exception:
            known = None
        if known:
            context.user_data["phone"] = known

    # якщо телефону немає – все одно приймаємо заявку, просто без номера
    full_name = ((user.first_name or "") + " " + (user.last_name or "")).strip()
    phone = context.user_data.get("phone", "")

    # запис у lead_messages (якщо БД є – запишеться, якщо ні – DummyConn залогить)
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
    await update.message.reply_text(
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

    # ------- зберігаємо коментар для медіа-кейсу (service/cable) -------
    flow = context.user_data.get("flow")
    if flow in ("service", "cable"):
        # якщо це не фраза типу "готово"/"це все" – вважаємо описом проблеми
        if "готово" not in lm and "це все" not in lm:
            context.user_data["media_comment"] = user_message

    # ===== Завершення медіа-кейсу ("Готово" / "Це все") =====
    media_case = context.user_data.get("media_case")
    if media_case:
        normalized = lm.strip()
        done_variants = {
            "готово",
            "готово.",
            "це все",
            "це все.",
            "все",
            "все.",
        }

        if normalized in done_variants:
            # пріоритет: явний comment_text у media_case,
            # якщо його немає — беремо те, що зберегли в media_comment
            comment_from_case = (media_case.get("comment_text") or "").strip()
            if not comment_from_case:
                comment_from_case = context.user_data.get("media_comment", "").strip()

            await finalize_media_case(
                update,
                context,
                comment_text=comment_from_case or None,
            )
            return

    # підтягуємо телефон із «постійної» таблиці, якщо ще не в user_data (тихо)
    if not context.user_data.get("phone"):
        try:
            known = db_get_known_phone_by_tg(str(user.id))
        except Exception:
            known = None
        if known:
            context.user_data["phone"] = known

    # тайм-аут сесії: якщо сплив — скидаємо діалог, але БЕЗ запиту телефону
    if session_expired(context):
        reset_session(context)
        await update.message.reply_text(
            "⏳ Сесію завершено через тривалу неактивність.\n"
            f"Я ваш помічник {F_COMPANY}. Можете продовжити діалог — просто напишіть запит.",
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

    # Якщо користувач сам надіслав номер (рядком) — можемо обробити як контакт
    # (але ми більше НЕ вимагаємо номер для роботи бота)
    maybe_phone = try_normalize_user_phone(user_message)
    if maybe_phone:
        await process_contact_submission(update, context, maybe_phone)
        return

    # Перше повідомлення → лід-стрічка (якщо БД / GSheets є)
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

    # ===== 1) KB-режим =====
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
            if str(MODEL_CHAT).startswith("gpt-5"):
                kwargs["max_completion_tokens"] = 1200
            else:
                kwargs["max_tokens"] = 1200
                kwargs["temperature"] = 0.2

            gpt_text = await with_thinking_timer(
                update,
                context,
                asyncio.to_thread(
                    openai_chat_with_retry,
                    kwargs,
                    label="KB",
                    max_attempts=2,
                ),
            )

            if gpt_text:
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

            logger.warning(
                "OpenAI KB empty answer after retry, falling back to web/plain."
            )
        except Exception as e:
            logger.error("OpenAI KB mode error: %s", e)

    # ===== 2) Web fallback =====
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
            if str(MODEL_CHAT).startswith("gpt-5"):
                kwargs["max_completion_tokens"] = 900
            else:
                kwargs["max_tokens"] = 900
                kwargs["temperature"] = 0.3

            gpt_text = await with_thinking_timer(
                update,
                context,
                asyncio.to_thread(
                    openai_chat_with_retry,
                    kwargs,
                    label="WEB",
                    max_attempts=1,
                ),
            )

            if gpt_text:
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

            logger.warning("OpenAI WEB empty answer, falling back to plain.")
        except Exception as e:
            logger.error("Web fallback error: %s", e)

    # ===== 3) Plain-режим (без KB / Web) =====
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
        if str(MODEL_CHAT).startswith("gpt-5"):
            kwargs["max_completion_tokens"] = 900
        else:
            kwargs["max_tokens"] = 900
            kwargs["temperature"] = 0.3

        gpt_text = await with_thinking_timer(
            update,
            context,
            asyncio.to_thread(
                openai_chat_with_retry,
                kwargs,
                label="PLAIN",
                max_attempts=2,
            ),
        )

        if not gpt_text:
            logger.warning(
                "OpenAI PLAIN empty answer after retry, showing stub to user."
            )
            await update.message.reply_text(
                "Вибачте, я тимчасово не можу сформувати відповідь. "
                "Спробуйте скоротити або спростити запит і надіслати ще раз.",
                reply_markup=bottom_keyboard(
                    context,
                    tg_user_id=str(update.effective_user.id),
                ),
            )
            return

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


async def with_thinking_timer(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    work_coro,
) -> str:
    """
    Обгортає будь-який "довгий" асинхронний виклик (GPT),
    показуючи користувачу таймер "Думаю… N с" і видаляючи його
    після завершення.

    work_coro — вже створений coroutine / task (наприклад, asyncio.to_thread(...)),
    який ми await-имо всередині.
    """
    msg = update.effective_message  # type: ignore[assignment]
    chat = update.effective_chat
    stop_event = asyncio.Event()
    timer_message: Message | None = None

    async def timer_worker():
        nonlocal timer_message

        # невелика затримка, щоб не мигало, якщо відповідь приходить миттєво
        await asyncio.sleep(2)
        if stop_event.is_set():
            return

        seconds = 2
        try:
            timer_message = await msg.reply_text(f"⌛ Думаю… {seconds} с")
        except Exception:
            timer_message = None

        # крутимося, поки не скажуть "стоп"
        while not stop_event.is_set():
            await asyncio.sleep(1)
            seconds += 1

            # паралельно періодично шлемо "typing", якщо є чат
            if chat is not None:
                with suppress(Exception):
                    await chat.send_action(ChatAction.TYPING)

            if not timer_message:
                continue

            try:
                await timer_message.edit_text(f"⌛ Думаю… {seconds} с")
            except Exception:
                # якщо не вийшло оновити — мовчки ігноруємо
                pass

    # запускаємо таймер у фоні
    timer_task = context.application.create_task(timer_worker())

    try:
        # чекаємо реального GPT-запиту
        result = await work_coro
    finally:
        # сигнал таймеру "стоп"
        stop_event.set()
        try:
            await timer_task
        except Exception:
            pass
        # видаляємо таймерне повідомлення, якщо воно є
        if timer_message:
            try:
                await timer_message.delete()
            except Exception:
                pass

    return result
