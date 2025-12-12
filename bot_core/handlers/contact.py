from telegram import Update
from telegram.ext import ContextTypes

from ..db import db_save_lead, db_set_known_phone
from ..utils import (
    ensure_dialog,
    try_normalize_user_phone,
    schedule_session_expiry,
    is_blacklisted,
    normalize_phone,
)
from ..ui import bottom_keyboard, main_menu_keyboard
from ..logging_setup import logger


async def process_contact_submission(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    normalized_phone: str,
):
    """
    Обробка збереження контакту:
    - оновлюємо сесію
    - пишемо лід у БД (якщо це НЕ співробітник)
    - зберігаємо телефон за tg_user_id
    - ставимо прапорець is_staff, якщо номер у blacklist
    """
    schedule_session_expiry(update, context)
    ensure_dialog(context)
    user = update.effective_user

    context.user_data["phone"] = normalized_phone
    context.user_data["first_q_saved"] = False

    # визначаємо, чи це "співробітник" (номер у спец-списку)
    is_special = is_blacklisted(normalized_phone)
    if is_special:
        context.user_data["is_staff"] = True

    saved = False
    if not is_special:
        # звичайні контакти йдуть у таблицю leads
        try:
            saved = db_save_lead(
                first_name=user.first_name or "",
                last_name=user.last_name or "",
                username=user.username or "",
                phone=normalized_phone,
            )
        except Exception as e:
            logger.error("DB save lead error: %s", e)

    # постійна «пам'ять» телефону за tg_user_id
    try:
        full_name = ((user.first_name or "") + " " + (user.last_name or "")).strip()
        db_set_known_phone(str(user.id), normalized_phone, full_name)
    except Exception as e:
        logger.error("DB set known phone error: %s", e)

    # текст шапки
    if is_special:
        head = (
            "Контакт прийнято ✅\n"
            "Ви авторизовані як співробітник FRENDT. "
            "Для вас доступна кнопка «Режим співробітника»."
        )
    else:
        head = "Дякуємо! Контакт збережено ✅" if saved else "Контакт уже є в системі ✅"

    # 1) відповідь з результатом + нижня клавіатура
    await update.message.reply_text(
        head,
        reply_markup=bottom_keyboard(context, tg_user_id=str(user.id)),
    )

    # 2) соцмережі + підказка про Меню
    await update.message.reply_text(
        "Підпишіться на наші соцмережі, щоб бути в курсі новин і корисних порад.\n"
        "Тепер можете натиснути «Меню» знизу або просто написати своє питання 👇",
        reply_markup=main_menu_keyboard(),
    )


async def on_contact(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Викликається, коли користувач ділиться контактом кнопкою.
    """
    contact = update.message.contact
    if not contact:
        await update.message.reply_text(
            "Не вдалося отримати номер. Спробуйте ще раз.",
            reply_markup=bottom_keyboard(
                context,
                tg_user_id=str(update.effective_user.id),
            ),
        )
        return

    raw_phone = contact.phone_number or ""
    # спочатку smart-нормалізація, потім жорстка
    norm = try_normalize_user_phone(raw_phone) or normalize_phone(raw_phone)

    await process_contact_submission(update, context, norm)


async def provide_contact(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Команда /contact або випадки, коли просимо поділитись номером.
    """
    schedule_session_expiry(update, context)
    await update.message.reply_text(
        "Будь ласка, поділіться своїм номером телефону кнопкою нижче:",
        reply_markup=bottom_keyboard(
            context,
            tg_user_id=str(update.effective_user.id),
        ),
    )
