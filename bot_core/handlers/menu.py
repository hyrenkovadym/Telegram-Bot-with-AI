# bot_core/handlers/menu.py
import time

from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes

from ..ui import bottom_keyboard
from ..utils import ensure_dialog, schedule_session_expiry


def main_menu_inline() -> InlineKeyboardMarkup:
    keyboard = [
        [
            InlineKeyboardButton("🚜 Автопілот", callback_data="menu:autopilot"),
            InlineKeyboardButton("📍 Навігація", callback_data="menu:navigation"),
        ],
        [
            InlineKeyboardButton("💧 Переобладнання обприскувача", callback_data="menu:seeder"),
            InlineKeyboardButton("🧪 Агрохімічні дослідження", callback_data="menu:agrochem"),
        ],
        [
            InlineKeyboardButton("📡 RTK-станції", callback_data="menu:rtk"),
            InlineKeyboardButton("🌾 Агрономічний консалтинг", callback_data="menu:agroconsult"),
        ],
        [
            InlineKeyboardButton("🔌 Кабельна продукція", callback_data="menu:cables"),
            InlineKeyboardButton("🛠 Сервіс", callback_data="menu:service"),
        ],
        [
            InlineKeyboardButton("🌍 Загальні питання", callback_data="menu:global"),
        ],
    ]
    return InlineKeyboardMarkup(keyboard)


def cables_submenu_inline() -> InlineKeyboardMarkup:
    keyboard = [
        [InlineKeyboardButton("🧵 Виготовити проводку", callback_data="menu:cables:make")],
        [InlineKeyboardButton("🔧 Відремонтувати проводку", callback_data="menu:cables:repair")],
        [InlineKeyboardButton("⬅️ Назад до меню", callback_data="menu:back")],
    ]
    return InlineKeyboardMarkup(keyboard)


def cables_repair_submenu_inline() -> InlineKeyboardMarkup:
    keyboard = [
        [InlineKeyboardButton("🔩 З вашими штекерами", callback_data="menu:cables:repair:own")],
        [InlineKeyboardButton("🧷 З нашими штекерами FRENDT", callback_data="menu:cables:repair:frendt")],
        [InlineKeyboardButton("⬅️ Назад до кабелів", callback_data="menu:cables")],
    ]
    return InlineKeyboardMarkup(keyboard)


def _reset_flows(ud: dict) -> None:
    # чистимо сценарії/медіакейси
    ud.pop("flow", None)
    ud.pop("cable_mode", None)
    ud.pop("media_case", None)
    ud.pop("service_photos", None)
    ud.pop("cable_photos", None)
    ud.pop("media_comment", None)
    ud.pop("flow_started_ts", None)


async def on_menu_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Натиснули reply-кнопку "Меню".
    1) Ставимо menu_open=True (щоб у reply-клавіатурі зникли "Меню/Менеджер" і лишився "Назад")
    2) Окремим повідомленням показуємо inline-меню.
    """
    schedule_session_expiry(update, context)
    ensure_dialog(context)

    _reset_flows(context.user_data)

    # відкрили меню — ховаємо "Меню" у reply клаві
    context.user_data["menu_open"] = True
    context.user_data["menu_shown_ts"] = time.time()

    await update.message.reply_text(
        "Меню відкрито. Оберіть пункт нижче або просто напишіть запитання.",
        reply_markup=bottom_keyboard(context, tg_user_id=str(update.effective_user.id)),
    )

    await update.message.reply_text(
        "Оберіть, будь ласка, що вас цікавить:",
        reply_markup=main_menu_inline(),
    )


async def on_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Обробка натискань inline-меню.
    Після вибору пункту закриваємо menu_open (щоб reply-кнопка "Меню" знову з’явилась).
    """
    query = update.callback_query
    await query.answer()

    schedule_session_expiry(update, context)
    ensure_dialog(context)

    data = (query.data or "").strip()
    parts = data.split(":")

    if len(parts) < 2 or parts[0] != "menu":
        return

    section = parts[1]

    # "Назад до меню" (inline)
    if section == "back":
        await query.message.reply_text(
            "Оберіть пункт меню:",
            reply_markup=main_menu_inline(),
        )
        return

    # Кабелі: показуємо підменю, НЕ закриваючи меню (reply лишається з "Назад")
    if section == "cables" and len(parts) == 2:
        context.user_data["section"] = "cables"
        await query.message.reply_text(
            "🔌 Кабельна продукція. Оберіть, що потрібно:",
            reply_markup=cables_submenu_inline(),
        )
        return

    # Кабелі: ремонт — ще підменю
    if section == "cables" and len(parts) == 3 and parts[2] == "repair":
        context.user_data["section"] = "cables"
        await query.message.reply_text(
            "🔧 Ремонт проводки. Оберіть варіант:",
            reply_markup=cables_repair_submenu_inline(),
        )
        return

    # Якщо користувач вибрав конкретну дію — тепер меню можна “закрити” (повернути звичайні кнопки)
    context.user_data["menu_open"] = False

    # Загальні питання
    if section == "global":
        _reset_flows(context.user_data)
        context.user_data["section"] = "global"
        await query.message.reply_text(
            "🌍 Загальні питання.\nНапишіть запит одним повідомленням.",
            reply_markup=bottom_keyboard(context, tg_user_id=str(query.from_user.id)),
        )
        return

    # Автопілот / Навігація / Обприскувач / Агрохімія / RTK / Консалтинг
    if section in {"autopilot", "navigation", "seeder", "agrochem", "rtk", "agroconsult"}:
        _reset_flows(context.user_data)
        context.user_data["section"] = section

        names = {
            "autopilot": "🚜 Автопілот",
            "navigation": "📍 Навігація",
            "seeder": "💧 Переобладнання обприскувача",
            "agrochem": "🧪 Агрохімічні дослідження",
            "rtk": "📡 RTK-станції",
            "agroconsult": "🌾 Агрономічний консалтинг",
        }

        await query.message.reply_text(
            f"{names.get(section, 'Розділ')}\n\n"
            "Напишіть, будь ласка:\n"
            "1) техніка/марка/модель\n"
            "2) що саме хочете отримати\n"
            "3) область/район (якщо про RTK/виїзд)\n",
            reply_markup=bottom_keyboard(context, tg_user_id=str(query.from_user.id)),
        )
        return

    # Сервіс
    if section == "service":
        _reset_flows(context.user_data)
        context.user_data["section"] = "service"
        context.user_data["flow"] = "service"
        context.user_data["service_photos"] = []
        context.user_data["flow_started_ts"] = time.time()

        await query.message.reply_text(
            "🛠 Сервіс.\n\n"
            "Коротко опишіть проблему і (за можливості) надішліть 2–5 фото:\n"
            "– загальний вигляд;\n"
            "– крупний план роз’ємів/помилок;\n"
            "– табличка моделі/серійник.\n",
            reply_markup=bottom_keyboard(context, tg_user_id=str(query.from_user.id)),
        )
        return

    # Кабелі: виготовлення
    if section == "cables" and len(parts) == 3 and parts[2] == "make":
        _reset_flows(context.user_data)
        context.user_data["section"] = "cables"
        context.user_data["flow"] = "cable"
        context.user_data["cable_mode"] = "make"
        context.user_data["cable_photos"] = []
        context.user_data["flow_started_ts"] = time.time()

        await query.message.reply_text(
            "🧵 Виготовлення проводки.\n\n"
            "Напишіть:\n"
            "1) техніка/марка/модель/рік\n"
            "2) що підключаємо (термінал/контролер/датчики)\n"
            "3) і надішліть фото роз’ємів крупним планом.\n",
            reply_markup=bottom_keyboard(context, tg_user_id=str(query.from_user.id)),
        )
        return

    # Кабелі: ремонт (з вашими штекерами)
    if section == "cables" and len(parts) == 4 and parts[2] == "repair" and parts[3] == "own":
        _reset_flows(context.user_data)
        context.user_data["section"] = "cables"
        context.user_data["flow"] = "cable"
        context.user_data["cable_mode"] = "repair_own"
        context.user_data["cable_photos"] = []
        context.user_data["flow_started_ts"] = time.time()

        await query.message.reply_text(
            "🔧 Ремонт проводки (з вашими штекерами).\n\n"
            "Надішліть фото штекерів + місця пошкодження, і напишіть техніку/модель.\n",
            reply_markup=bottom_keyboard(context, tg_user_id=str(query.from_user.id)),
        )
        return

    # Кабелі: ремонт (з нашими штекерами)
    if section == "cables" and len(parts) == 4 and parts[2] == "repair" and parts[3] == "frendt":
        _reset_flows(context.user_data)
        context.user_data["section"] = "cables"
        context.user_data["flow"] = "cable"
        context.user_data["cable_mode"] = "repair_frendt"
        context.user_data["cable_photos"] = []
        context.user_data["flow_started_ts"] = time.time()

        await query.message.reply_text(
            "🔧 Ремонт/виготовлення проводки (з нашими штекерами FRENDT).\n\n"
            "Надішліть фото місця підключення та роз’ємів, і напишіть техніку/модель.\n",
            reply_markup=bottom_keyboard(context, tg_user_id=str(query.from_user.id)),
        )
        return

    # дефолт
    await query.message.reply_text(
        "Ок. Напишіть запит одним повідомленням.",
        reply_markup=bottom_keyboard(context, tg_user_id=str(query.from_user.id)),
    )
