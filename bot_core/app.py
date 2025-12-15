# bot_core/app.py
import os
import re
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
)

from .config import (
    TELEGRAM_TOKEN,
    PORT,
    WEBHOOK_BASE,
    WEBHOOK_PATH,
    TELEGRAM_SECRET,
    MANAGER_BTN,
    MENU_BTN,
    STAFF_BTN,
    BACK_BTN,
)
from .logging_setup import logger
from . import utils as utils_mod
from .utils import reload_blacklist
from .kb import kb_build_or_load

from .db import db_init
from .gsheets import gsheet_append_row, gsheet_append_event  # noqa: F401

from .handlers.core import (
    cmd_start,
    cmd_model,
    cmd_reload_blacklist,
    cmd_reload_kb,
    cmd_last,
    cmd_phone_mode,   # NEW
    cmd_set_model,    # NEW
    cmd_admin_state,  # NEW
    handle_message,
    block_non_text,
    on_manager_request,
)
from .handlers.contact import on_contact, provide_contact
from .handlers.menu import on_menu_button, on_menu_callback
from .handlers.staff import on_staff_button, on_staff_back
from .handlers.voice import on_voice_message
from .handlers.media import on_photo_message


def choose_run_mode() -> str:
    """
    Вибір режиму запуску:
    - якщо RUN_MODE=polling → polling
    - якщо RUN_MODE=webhook і є WEBHOOK_BASE → webhook
    - якщо нічого не задано, але WEBHOOK_BASE порожній → polling
    """
    env_mode = (os.getenv("RUN_MODE", "") or "").strip().lower()
    webhook_base = (os.getenv("WEBHOOK_BASE", "") or "").strip()
    print(
        f"[diag] RUN_MODE={env_mode or '∅'} "
        f"WEBHOOK_BASE={'set' if webhook_base else '∅'} "
        f"PORT={os.getenv('PORT', '∅')}"
    )

    if env_mode.startswith("poll"):
        return "polling"
    if env_mode.startswith("web") and webhook_base:
        return "webhook"
    if not webhook_base:
        return "polling"
    return "polling"


def build_app() -> Application:
    """
    Створює та налаштовує Application з усіма хендлерами.
    """
    if not TELEGRAM_TOKEN:
        raise RuntimeError("TELEGRAM_TOKEN не заданий у .env")

    app = Application.builder().token(TELEGRAM_TOKEN).build()

    # Голосові та аудіо → в наш voice-handler
    app.add_handler(MessageHandler(filters.VOICE | filters.AUDIO, on_voice_message))

    # Фото (для сервісу та кабельної проводки)
    app.add_handler(MessageHandler(filters.PHOTO, on_photo_message))

    # Кнопка "Зв’язатись з менеджером"
    app.add_handler(
        MessageHandler(
            filters.TEXT & filters.Regex(rf"^{re.escape(MANAGER_BTN)}$"),
            on_manager_request,
        )
    )

    # Кнопка "Меню"
    app.add_handler(
        MessageHandler(
            filters.TEXT & filters.Regex(rf"^{re.escape(MENU_BTN)}$"),
            on_menu_button,
        )
    )

    # Кнопка "Режим співробітника"
    app.add_handler(
        MessageHandler(
            filters.TEXT & filters.Regex(rf"^{re.escape(STAFF_BTN)}$"),
            on_staff_button,
        )
    )

    # Кнопка "Назад" (вихід із режиму співробітника)
    app.add_handler(
        MessageHandler(
            filters.TEXT & filters.Regex(rf"^{re.escape(BACK_BTN)}$"),
            on_staff_back,
        )
    )

    # callback-и меню (8 пунктів + підменю)
    app.add_handler(CallbackQueryHandler(on_menu_callback, pattern=r"^menu:"))

    # Команди
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("contact", provide_contact))
    app.add_handler(CommandHandler("reload_blacklist", cmd_reload_blacklist))
    app.add_handler(CommandHandler("last", cmd_last))
    app.add_handler(CommandHandler("model", cmd_model))
    app.add_handler(CommandHandler("reload_kb", cmd_reload_kb))

    # адмін-команди
    app.add_handler(CommandHandler("phone_mode", cmd_phone_mode))
    app.add_handler(CommandHandler("set_model", cmd_set_model))
    app.add_handler(CommandHandler("admin_state", cmd_admin_state))

    # Контакт (поділитися номером)
    app.add_handler(MessageHandler(filters.CONTACT, on_contact))

    # Усі текстові повідомлення → в загальний handler
    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            handle_message,
        )
    )

    # Все інше не-текстове (документи, стікери тощо) → блокуємо
    app.add_handler(
        MessageHandler(
            ~filters.TEXT
            & ~filters.COMMAND
            & ~filters.VOICE
            & ~filters.AUDIO
            & ~filters.PHOTO
            & ~filters.CONTACT,
            block_non_text,
        )
    )

    return app


def run_webhook(app: Application):
    """
    Запуск у режимі WEBHOOK (наприклад, на Cloud Run/Render/Fly.io).
    """
    if not WEBHOOK_BASE:
        raise RuntimeError(
            "WEBHOOK_BASE не заданий (потрібен публічний URL сервісу)."
        )

    print(
        f"✅ Бот запущений у режимі WEBHOOK на PORT={PORT} PATH={WEBHOOK_PATH} BASE={WEBHOOK_BASE}"
    )
    app.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        url_path=WEBHOOK_PATH.lstrip("/"),
        secret_token=TELEGRAM_SECRET,
        webhook_url=f"{WEBHOOK_BASE}{WEBHOOK_PATH}",
    )


def run_polling(app: Application):
    """
    Запуск у режимі POLLING (локально / на простому VPS).
    """
    logger.info("Старт у режимі POLLING.")
    print("✅ Бот запущений у режимі POLLING.")
    app.run_polling()


def main():
    """
    Точка входу:
    - ініціалізація БД
    - підвантаження blacklist
    - побудова/завантаження KB
    - перевірка Google Sheets
    - старт бота у відповідному режимі
    """
    db_init()
    count = reload_blacklist()
    logger.info("Blacklist loaded: %s numbers", count)

    # KB — пишемо індекс в utils_mod._KB_INDEX
    try:
        idx = kb_build_or_load()
        utils_mod._KB_INDEX = idx
        logger.info("[KB] Готово. Фрагментів: %d", len(idx.get("chunks", [])))
    except Exception as e:
        logger.warning("[KB] Не вдалося побудувати індекс: %s", e)

    # Попередження про Google Sheets JSON
    from .config import GSHEET_PATH, GSHEET_NAME

    if not os.path.exists(GSHEET_PATH):
        logger.warning(
            "[GSHEET] JSON файл не знайдено: %s. Надішліть сервісному e-mail доступ до '%s'.",
            GSHEET_PATH,
            GSHEET_NAME,
        )

    app = build_app()

    mode = choose_run_mode()
    if mode == "webhook":
        logger.info(
            "Старт у режимі WEBHOOK на PORT=%s PATH=%s",
            PORT,
            WEBHOOK_PATH,
        )
        run_webhook(app)
    else:
        run_polling(app)


if __name__ == "__main__":
    main()
