# bot_core/handlers/photo.py
import time
from telegram import Update
from telegram.ext import ContextTypes

from ..ui import bottom_keyboard
from ..utils import ensure_dialog, schedule_session_expiry
from ..logging_setup import logger


def _flow_timed_out(user_data: dict, timeout_sec: int = 15 * 60) -> bool:
    ts = user_data.get("flow_started_ts")
    return bool(ts and time.time() - ts > timeout_sec)


def _reset_special_flow(user_data: dict):
    for key in (
        "flow",
        "flow_started_ts",
        "service_stage",
        "service_desc",
        "service_photos",
        "cable_mode",
        "cable_stage",
        "cable_photos",
    ):
        user_data.pop(key, None)


async def on_photo_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Обробка будь-яких фото:
    - якщо активний флоу 'Сервіс' або 'Кабельна продукція' → зберігаємо file_id у user_data
    - інакше відповідаємо, що фото краще надсилати менеджеру.
    """
    schedule_session_expiry(update, context)
    ensure_dialog(context)

    user = update.effective_user

    # Тайм-аут 15 хвилин для спец-флоу
    if _flow_timed_out(context.user_data):
        _reset_special_flow(context.user_data)

    flow = context.user_data.get("flow")
    service_stage = context.user_data.get("service_stage")
    cable_stage = context.user_data.get("cable_stage")

    photo = update.message.photo[-1]
    file_id = photo.file_id

    # ----- СЕРВІС -----
    if flow == "service":
        photos = context.user_data.get("service_photos") or []
        photos.append(file_id)
        context.user_data["service_photos"] = photos

        # Якщо ще не були на етапі фото — вважаємо, що вже тут
        if service_stage != "await_photos":
            context.user_data["service_stage"] = "await_photos"

        logger.info("SERVICE photo from %s: %s", user.id, file_id)

        await update.message.reply_text(
            "Фото збережено ✅ Якщо є ще — надсилайте.\n"
            "Коли все надішлете — напишіть «Готово».",
            reply_markup=bottom_keyboard(context, tg_user_id=str(user.id)),
        )
        return

    # ----- КАБЕЛЬНА ПРОВОДКА -----
    if flow == "cable":
        photos = context.user_data.get("cable_photos") or []
        photos.append(file_id)
        context.user_data["cable_photos"] = photos

        mode = context.user_data.get("cable_mode", "make")
        logger.info("CABLE (%s) photo from %s: %s", mode, user.id, file_id)

        await update.message.reply_text(
            "Фото збережено ✅ Якщо є ще — надсилайте.\n"
            "Коли все надішлете — напишіть «Готово».",
            reply_markup=bottom_keyboard(context, tg_user_id=str(user.id)),
        )
        return

    # ----- Немає активного спец-флоу -----
    await update.message.reply_text(
        "Фото краще надіслати безпосередньо менеджеру, щоб він міг їх оцінити.\n"
        "Якщо хочете створити сервісну заявку чи запит на проводку — "
        "натисніть «Меню» і оберіть «Сервіс» або «Кабельна продукція».",
        reply_markup=bottom_keyboard(context, tg_user_id=str(user.id)),
    )
