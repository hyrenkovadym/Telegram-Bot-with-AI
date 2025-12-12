from telegram import Update
from telegram.ext import ContextTypes

from ..utils import ensure_dialog, schedule_session_expiry
from ..db import db_get_known_phone_by_tg, db_save_first_message
from ..gsheets import gsheet_append_event
from ..ui import bottom_keyboard


async def on_manager_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
    schedule_session_expiry(update, context)
    ensure_dialog(context)

    if not context.user_data.get("phone"):
        try:
            known = db_get_known_phone_by_tg(str(update.effective_user.id))
        except Exception:
            known = None
        if known:
            context.user_data["phone"] = known

    if not context.user_data.get("phone"):
        await update.message.reply_text(
            "Щоб менеджер зміг з вами зв’язатися, будь ласка, спочатку поділіться своїм номером телефону.",
            reply_markup=bottom_keyboard(context, tg_user_id=str(update.effective_user.id)),
        )
        return

    user = update.effective_user
    full_name = ((user.first_name or "") + " " + (user.last_name or "")).strip()
    phone = context.user_data.get("phone", "")

    try:
        db_save_first_message(
            phone=phone,
            full_name=full_name,
            text="Заявка: зв’язок з менеджером",
            tg_user_id=str(user.id),
        )
    except Exception as e:
        from ..logging_setup import logger
        logger.error("DB save manager request error: %s", e)

    gsheet_append_event("Заявка: зв’язок з менеджером", full_name=full_name, phone=phone)

    await update.message.reply_text(
        "Передав менеджеру вашу заявку. Очікуйте на дзвінок або відповідь найближчим часом.",
        reply_markup=bottom_keyboard(context, tg_user_id=str(update.effective_user.id)),
    )
