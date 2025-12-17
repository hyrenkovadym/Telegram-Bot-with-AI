from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import ContextTypes

from .config import MANAGER_BTN, MENU_BTN, STAFF_BTN, BACK_BTN, ADMIN_IDS
from .db import db_get_known_phone_by_tg
from .utils import is_staff_phone


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
    - Якщо меню ще не відкривали → [Меню] (+ [Режим співробітника], якщо дозволено)
    - Якщо меню вже відкривали → [Зв’язатись з менеджером] (+ [Режим співробітника], якщо дозволено)

    ВАЖЛИВО: телефон НЕ вимагаємо взагалі (щоб після "сну" Render не просило контакт по новій).
    """

    # 1) staff mode → лише Назад
    if context.user_data.get("staff_mode"):
        return ReplyKeyboardMarkup(
            [[BACK_BTN]],
            resize_keyboard=True,
            one_time_keyboard=False,
            selective=False,
        )

    # 2) підтягнемо телефон з БД, якщо він колись був (не обов'язково)
    known_phone = None
    if tg_user_id:
        try:
            known_phone = db_get_known_phone_by_tg(str(tg_user_id))
        except Exception:
            known_phone = None

        if known_phone and not context.user_data.get("phone"):
            context.user_data["phone"] = known_phone

    phone = context.user_data.get("phone") or known_phone

    # 3) staff дозволений або по phone з файлу, або по tg id (ADMIN)
    tg_int = None
    try:
        tg_int = int(tg_user_id) if tg_user_id else None
    except Exception:
        tg_int = None

    staff_allowed = False
    if phone and is_staff_phone(phone):
        staff_allowed = True
    if tg_int is not None and tg_int in (ADMIN_IDS or []):
        staff_allowed = True

    menu_shown = bool(context.user_data.get("menu_shown"))
    rows = []

    if not menu_shown:
        rows.append([MENU_BTN])
    else:
        rows.append([MANAGER_BTN])

    if staff_allowed:
        rows.append([STAFF_BTN])

    return ReplyKeyboardMarkup(
        rows,
        resize_keyboard=True,
        one_time_keyboard=False,
        selective=False,
    )
