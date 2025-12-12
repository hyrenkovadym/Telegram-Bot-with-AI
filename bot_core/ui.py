from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import ContextTypes

from .config import MANAGER_BTN, MENU_BTN, STAFF_BTN, BACK_BTN
from .db import db_get_known_phone_by_tg
from .utils import is_staff_phone   # ⬅️ ДОДАЛИ ІМПОРТ


def main_menu_keyboard():
    keyboard = [
        [InlineKeyboardButton("📸 Instagram", url="https://www.instagram.com/frendt_llc")],
        [InlineKeyboardButton("🎵 TikTok", url="https://www.tiktok.com/@frendt_life?_r=1&_t=ZM-910jkI6EXed")],
        [InlineKeyboardButton("🌐 Вебсайт FRENDT", url="https://frendt.ua/")],
    ]
    return InlineKeyboardMarkup(keyboard)


def bottom_keyboard(context: ContextTypes.DEFAULT_TYPE, tg_user_id: str | None = None):
    """
    Динамічна нижня клавіатура:

    - Якщо staff_mode=True  → тільки [Назад]
    - Якщо телефону ще немає → [Поділитись номером]
    - Якщо телефон є, але меню ще не відкривали → [Меню] (+ [Режим співробітника], якщо це співробітник)
    - Якщо телефон є і меню вже відкривали → [Зв’язатись з менеджером] (+ [Режим співробітника], якщо це співробітник)
    """

    # 1) Якщо зараз увімкнутий режим співробітника — показуємо лише «Назад»
    if context.user_data.get("staff_mode"):
        return ReplyKeyboardMarkup(
            [[BACK_BTN]],
            resize_keyboard=True,
            one_time_keyboard=False,
            selective=False,
        )

    # 2) Підтягуємо телефон із БД, якщо треба
    known_phone = None
    if tg_user_id:
        try:
            known_phone = db_get_known_phone_by_tg(str(tg_user_id))
        except Exception:
            known_phone = None

        if known_phone and not context.user_data.get("phone"):
            context.user_data["phone"] = known_phone

    # поточний телефон користувача
    phone = context.user_data.get("phone") or known_phone
    has_phone = bool(phone)

    # 3) Якщо телефону ще немає — просимо поділитись
    if not has_phone:
        return ReplyKeyboardMarkup(
            [[KeyboardButton("Поділитись номером", request_contact=True)]],
            resize_keyboard=True,
            one_time_keyboard=False,
            selective=False,
        )

    # 4) Є телефон → визначаємо, чи це співробітник
    staff_allowed = is_staff_phone(phone)

    menu_shown = bool(context.user_data.get("menu_shown"))
    rows = []

    if not menu_shown:
        # Телефон є, але меню ще не відкривали → спочатку «Меню»
        rows.append([MENU_BTN])
    else:
        # Меню вже відкривали → «Менеджер»
        rows.append([MANAGER_BTN])

    # Додаємо "Режим співробітника" тільки якщо номер зі списку staff
    if staff_allowed:
        rows.append([STAFF_BTN])

    return ReplyKeyboardMarkup(
        rows,
        resize_keyboard=True,
        one_time_keyboard=False,
        selective=False,
    )
