# bot_core/handlers/media.py
from telegram import Update
from telegram.ext import ContextTypes

from ..logging_setup import logger
from ..utils import ensure_dialog, schedule_session_expiry, touch_session
from ..ui import bottom_keyboard
from ..drive_media import add_photo_to_media_case
from ..cable_ai import classify_cable_or_connector_from_photo


async def on_photo_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Обробка фото:
    - якщо активний flow (service/cable/ін.) → відправляємо фото в Google Drive
      + (для flow='cable') пробуємо визначити тип кабелю/роз'єму через ШІ
        і зберігаємо результат у media_case, але клієнту показуємо
        фінальний висновок вже на етапі «Готово».
    - інакше → просимо надсилати текст (як block_non_text).
    """
    schedule_session_expiry(update, context)
    ensure_dialog(context)
    touch_session(context)

    if not update.message or not update.message.photo:
        return

    ud = context.user_data
    flow = ud.get("flow")  # "service", "cable" або None

    # якщо немає активного сценарію і ще не створено media_case —
    # просимо спочатку обрати розділ у меню
    if not flow and not ud.get("media_case"):
        await update.message.reply_text(
            "Будь ласка, надсилайте текстове повідомлення 💬.\n"
            "Якщо хочете відправити фото для сервісу або кабельної продукції — "
            "спочатку оберіть відповідний розділ у меню.",
            reply_markup=bottom_keyboard(
                context,
                tg_user_id=str(update.effective_user.id),
            ),
        )
        return

    try:
        # беремо фото найкращої якості
        photo = update.message.photo[-1]
        tg_file = await photo.get_file()
        file_bytes = await tg_file.download_as_bytearray()
        filename = f"{photo.file_unique_id}.jpg"

        # Спочатку зберігаємо в Google Drive (створить/поновить media_case)
        await add_photo_to_media_case(update, context, bytes(file_bytes), filename)

        # ====== ШІ-КЛАСИФІКАЦІЯ ДЛЯ КАБЕЛЬНОГО ФЛОУ ======
        if flow == "cable":
            try:
                ai_result = await classify_cable_or_connector_from_photo(
                    bytes(file_bytes),
                    flow="cable",
                )
            except Exception as e:
                logger.error("[PHOTO] cable AI classify error: %s", e)
                ai_result = None

            if ai_result:
                # Зберігаємо попередній тип кабелю в media_case,
                # щоб потім використати у finalize_media_case()
                case = ud.get("media_case") or {}
                case["detected_cable"] = ai_result
                ud["media_case"] = case

    except Exception as e:
        logger.error("[PHOTO] error while processing photo: %s", e)
        await update.message.reply_text(
            "Не вдалося обробити це фото. Спробуйте, будь ласка, ще раз.",
            reply_markup=bottom_keyboard(
                context,
                tg_user_id=str(update.effective_user.id),
            ),
        )
