# bot_core/handlers/menu.py
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes

from ..ui import bottom_keyboard
from ..logging_setup import logger
from ..utils import ensure_dialog, schedule_session_expiry


def main_menu_inline():
    keyboard = [
        [
            InlineKeyboardButton("🚜 Автопілот", callback_data="menu:autopilot"),
            InlineKeyboardButton("📍 Навігація", callback_data="menu:navigation"),
        ],
        [
            InlineKeyboardButton(
                "💧 Переобладнання обприскувача",
                callback_data="menu:seeder",
            ),
            InlineKeyboardButton(
                "🧪 Агрохімічні дослідження",
                callback_data="menu:agrochem",
            ),
        ],
        [
            InlineKeyboardButton("📡 RTK-станції", callback_data="menu:rtk"),
            InlineKeyboardButton(
                "🌾 Агрономічний консалтинг",
                callback_data="menu:agroconsult",
            ),
        ],
        [
            InlineKeyboardButton("🔌 Кабельна продукція", callback_data="menu:cables"),
            InlineKeyboardButton("🛠 Сервіс", callback_data="menu:service"),
        ],
        [
            InlineKeyboardButton(
                "🌍 Загальні питання",
                callback_data="menu:global",
            ),
        ],
    ]
    return InlineKeyboardMarkup(keyboard)



def cables_submenu_inline():
    keyboard = [
        [
            InlineKeyboardButton(
                "🧵 Виготовити проводку",
                callback_data="menu:cables:make",
            ),
        ],
        [
            InlineKeyboardButton(
                "🔧 Відремонтувати проводку",
                callback_data="menu:cables:repair",
            ),
        ],
    ]
    return InlineKeyboardMarkup(keyboard)


def cables_repair_submenu_inline():
    keyboard = [
        [
            InlineKeyboardButton(
                "🔩 З вашими штекерами",
                callback_data="menu:cables:repair:own",
            ),
        ],
        [
            InlineKeyboardButton(
                "🧷 З нашими штекерами FRENDT",
                callback_data="menu:cables:repair:frendt",
            ),
        ],
    ]
    return InlineKeyboardMarkup(keyboard)


def _reset_special_flow(user_data: dict):
    """
    Скидаємо стан сценаріїв 'Сервіс' / 'Кабельна продукція'.
    Використовуємо, коли стартуємо новий флоу з меню.
    """
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


async def on_menu_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Reply-кнопка 'Меню'.
    Показуємо основне меню з 8 пунктів (2 колонки).
    """
    schedule_session_expiry(update, context)
    ensure_dialog(context)

    _reset_special_flow(context.user_data)

    await update.message.reply_text(
        "Оберіть, будь ласка, що вас цікавить, або просто напишіть своє питання:",
        reply_markup=main_menu_inline(),
    )


async def on_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Обробка натискань на інлайн-кнопки меню:
    - menu:service
    - menu:cables / :make / :repair / :repair:own / :repair:frendt
    - інші розділи
    """
    query = update.callback_query
    await query.answer()
    schedule_session_expiry(update, context)
    ensure_dialog(context)

    data = query.data or ""
    parts = data.split(":")

    if not parts or parts[0] != "menu":
        logger.warning("Unknown menu callback: %s", data)
        return

    # Запам’ятовуємо поточний розділ для GPT / аналітики
    if len(parts) >= 2:
        context.user_data["section"] = parts[1]

            # Якщо обрали "Загальні питання" — скидаємо спецфлоу (сервіс/кабелі)
    if len(parts) == 2 and parts[1] == "global":
        _reset_special_flow(context.user_data)


    # ----- СЕРВІС -----
    if len(parts) == 2 and parts[1] == "service":
        _reset_special_flow(context.user_data)
        import time

        context.user_data["flow"] = "service"
        context.user_data["service_stage"] = "await_description"
        context.user_data["service_desc"] = ""
        context.user_data["service_photos"] = []
        context.user_data["flow_started_ts"] = time.time()

        await query.message.reply_text(
            "🛠 Ви в розділі «Сервіс».\n\n"
            "Опишіть, будь ласка, вашу проблему:\n"
            "– яка техніка;\n"
            "– яка система (TerraNavix / Hexagon / інша);\n"
            "– що саме не працює або яку помилку бачите.",
            reply_markup=bottom_keyboard(context, tg_user_id=str(query.from_user.id)),
        )
        return

    # ----- КАБЕЛЬНА ПРОДУКЦІЯ -----
    if len(parts) >= 2 and parts[1] == "cables":
        # Верхній рівень
        if len(parts) == 2:
            _reset_special_flow(context.user_data)
            await query.message.reply_text(
                "🔌 Ви в розділі «Кабельна продукція». Що потрібно?",
                reply_markup=cables_submenu_inline(),
            )
            return

        # Виготовлення
        if len(parts) == 3 and parts[2] == "make":
            _reset_special_flow(context.user_data)
            import time

            context.user_data["flow"] = "cable"
            context.user_data["cable_mode"] = "make"
            context.user_data["cable_stage"] = "await_photos"
            context.user_data["cable_photos"] = []
            context.user_data["flow_started_ts"] = time.time()

            await query.message.reply_text(
                "🧵 Виготовлення проводки.\n\n"
                "Надішліть, будь ласка, 2–5 фото:\n"
                "– загальний вигляд джгута / місця, де має бути проводка;\n"
                "– крупним планом роз’єми та місця підключення.\n\n"
                "Коли все надішлете — напишіть «Готово».",
                reply_markup=bottom_keyboard(context, tg_user_id=str(query.from_user.id)),
            )
            return

        # Ремонт → обрати підрежим
        if len(parts) == 3 and parts[2] == "repair":
            _reset_special_flow(context.user_data)
            await query.message.reply_text(
                "🔧 Ремонт проводки. Оберіть варіант:",
                reply_markup=cables_repair_submenu_inline(),
            )
            return

        # Ремонт з деталізацією
        if len(parts) == 4 and parts[2] == "repair":
            mode_tail = parts[3]
            if mode_tail == "own":
                mode = "repair_own"
                text = (
                    "🔧 Ремонт проводки з ВАШИМИ штекерами.\n\n"
                    "Надішліть 2–5 фото:\n"
                    "– загальний вигляд проводки;\n"
                    "– крупним планом кожен штекер;\n"
                    "– місця пошкодження.\n\n"
                    "Коли все надішлете — напишіть «Готово»."
                )
            else:
                mode = "repair_frendt"
                text = (
                    "🧷 Ремонт проводки з НАШИМИ штекерами FRENDT.\n\n"
                    "Надішліть 2–5 фото:\n"
                    "– загальний вигляд проводки / місця встановлення;\n"
                    "– роз’єми, до яких потрібно підключитись;\n"
                    "– місця пошкодження (якщо є).\n\n"
                    "Коли все надішлете — напишіть «Готово»."
                )

            _reset_special_flow(context.user_data)
            import time

            context.user_data["flow"] = "cable"
            context.user_data["cable_mode"] = mode
            context.user_data["cable_stage"] = "await_photos"
            context.user_data["cable_photos"] = []
            context.user_data["flow_started_ts"] = time.time()

            await query.message.reply_text(
                text,
                reply_markup=bottom_keyboard(context, tg_user_id=str(query.from_user.id)),
            )
            return

    # ----- Інші стандартні розділи (без жорстких шаблонів відповідей) -----
    section = parts[1] if len(parts) > 1 else ""
    text = ""

    if section == "autopilot":
        text = (
            "🚜 Ви обрали розділ «Автопілот».\n"
            "Напишіть марку/модель трактора, площу і які задачі хочете вирішити — "
            "далі я уточню деталі та підберу варіанти."
        )
    elif section == "navigation":
        text = (
            "📍 Ви обрали розділ «Навігація».\n"
            "Опишіть, будь ласка, що саме потрібно: паралельне водіння, облік робіт, інтеграція тощо."
        )
    elif section == "seeder":
        text = (
            "💧 Ви обрали розділ «Переобладнання обприскувача».\n"
            "Напишіть марку/модель обприскувача та що хочете переобладнати — далі уточню деталі."
        )
    elif section == "agrochem":
        text = (
            "🧪 Ви обрали розділ «Агрохімічні дослідження».\n"
            "Можете поставити будь-яке питання щодо аналізу ґрунту, карт забезпеченості або VRA-внесення."
        )
    elif section == "rtk":
        text = (
            "📡 Ви обрали розділ «RTK-станції».\n"
            "Напишіть область/район та яку техніку плануєте підключати — далі я вже розпитаю детальніше."
        )
    elif section == "agroconsult":
        text = (
            "🌾 Ви обрали розділ «Агрономічний консалтинг».\n"
            "Опишіть свою ситуацію або питання по технології — я дам базову відповідь і підкажу наступні кроки."
        )

    elif section == "global":
        text = (
            "🌍 Ви обрали розділ «Загальні питання».\n"
            "Тут можна ставити будь-які запитання — не лише про агро чи FRENDT.\n"
            "Просто напишіть, що вас цікавить 🙂"
        )


    if text:
        await query.message.reply_text(
            text,
            reply_markup=bottom_keyboard(context, tg_user_id=str(query.from_user.id)),
        )
    else:
        logger.warning("Unhandled menu section: %s", data)
