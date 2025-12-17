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
    choose_run_mode,
)
from .logging_setup import logger
from . import utils as utils_mod
from .utils import reload_blacklist
from .kb import kb_build_or_load

from .db import db_init

from .handlers.core import (
    cmd_start,
    cmd_model,
    cmd_reload_blacklist,
    cmd_reload_kb,
    cmd_last,
    handle_message,
    block_non_text,
    on_manager_request,
)
from .handlers.contact import on_contact, provide_contact
from .handlers.menu import on_menu_button, on_menu_callback
from .handlers.staff import on_staff_button, on_staff_back
from .handlers.voice import on_voice_message
from .handlers.media import on_photo_message


def build_app() -> Application:
    if not TELEGRAM_TOKEN:
        raise RuntimeError("TELEGRAM_TOKEN не заданий у .env")

    app = Application.builder().token(TELEGRAM_TOKEN).build()

    # Голосові та аудіо → voice-handler
    app.add_handler(MessageHandler(filters.VOICE | filters.AUDIO, on_voice_message))

    # Фото → photo-handler
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

    # Кнопка "Назад"
    app.add_handler(
        MessageHandler(
            filters.TEXT & filters.Regex(rf"^{re.escape(BACK_BTN)}$"),
            on_staff_back,
        )
    )

    # callback-и меню
    app.add_handler(CallbackQueryHandler(on_menu_callback, pattern=r"^menu:"))

    # Команди
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("contact", provide_contact))
    app.add_handler(CommandHandler("reload_blacklist", cmd_reload_blacklist))
    app.add_handler(CommandHandler("last", cmd_last))
    app.add_handler(CommandHandler("model", cmd_model))
    app.add_handler(CommandHandler("reload_kb", cmd_reload_kb))

    # Контакт
    app.add_handler(MessageHandler(filters.CONTACT, on_contact))

    # Усі текстові повідомлення → core handler
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # Все інше
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
    if not WEBHOOK_BASE:
        raise RuntimeError("WEBHOOK_BASE не заданий (потрібен публічний URL сервісу).")

    print(f"✅ Бот запущений у режимі WEBHOOK на PORT={PORT} PATH={WEBHOOK_PATH} BASE={WEBHOOK_BASE}")
    app.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        url_path=WEBHOOK_PATH.lstrip("/"),
        secret_token=TELEGRAM_SECRET,
        webhook_url=f"{WEBHOOK_BASE}{WEBHOOK_PATH}",
    )


def run_polling(app: Application):
    logger.info("Старт у режимі POLLING.")
    print("✅ Бот запущений у режимі POLLING.")
    app.run_polling()


def main():
    db_init()

    count = reload_blacklist()
    logger.info("Blacklist loaded: %s numbers", count)

    # KB
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
        logger.info("Старт у режимі WEBHOOK на PORT=%s PATH=%s", PORT, WEBHOOK_PATH)
        run_webhook(app)
    else:
        run_polling(app)


if __name__ == "__main__":
    main()
