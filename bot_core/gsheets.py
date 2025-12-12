# bot_core/gsheets.py
import os
from datetime import datetime
from typing import List, Optional

import gspread
from google.oauth2.service_account import Credentials

from .logging_setup import logger

# ---- ENV ----
SERVICE_JSON = os.getenv("GSHEET_SERVICE_JSON", "frendt-service.json")

GSHEET_NAME = os.getenv("GSHEET_NAME", "FRENDT Leads")

# каталог асортименту (окремий файл)
GSHEET_CATALOG_ID = os.getenv("GSHEET_CATALOG_ID", "").strip()
GSHEET_CATALOG_SHEET_CABLES = os.getenv(
    "GSHEET_CATALOG_SHEET_CABLES", "Кабелі"
).strip()
GSHEET_CATALOG_SHEET_CONNECTORS = os.getenv(
    "GSHEET_CATALOG_SHEET_CONNECTORS", "Роз'єми"
).strip()


# окремий файл під медіа (не обов'язковий)
GSHEET_MEDIA_NAME = os.getenv("GSHEET_MEDIA_NAME", "FRENDT Bot Media")

_SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

_client = None


def _get_client():
    global _client
    if _client is not None:
        return _client

    if not os.path.exists(SERVICE_JSON):
        logger.error("[GSHEET] service json not found: %s", SERVICE_JSON)
        return None

    creds = Credentials.from_service_account_file(SERVICE_JSON, scopes=_SCOPES)
    _client = gspread.authorize(creds)
    logger.info("[GSHEET] client initialised OK.")
    return _client


def _open_sheet_by_name(name: str):
    client = _get_client()
    if client is None:
        return None
    try:
        sh = client.open(name)
        ws = sh.sheet1
        return ws
    except Exception as e:
        logger.error("[GSHEET] open sheet '%s' error: %s", name, e)
        return None


# ===== СТАРІ ФУНКЦІЇ (lead-таблиця) =====
def gsheet_append_row(*, full_name: str, phone: str, message: str):
    """
    Додаємо звичайний рядок у таблицю лідів (як і раніше).
    """
    ws = _open_sheet_by_name(GSHEET_NAME)
    if ws is None:
        return

    created = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    row = [created, full_name, phone, message]
    try:
        ws.insert_row(row, index=2)
        logger.info("[GSHEET] inserted row at top (row 2)")
    except Exception as e:
        logger.error("[GSHEET] insert row error: %s", e)


def gsheet_append_row_with_media(
    *, full_name: str, phone: str, comment: str, media_url: str
):
    """
    Додає рядок у головну таблицю FRENDT Leads з колонкою для URL медіа.
    A: дата/час, B: ім'я, C: номер, D: коментар, E: Медіа/Фото URL.
    """
    ws = _open_sheet_by_name(GSHEET_NAME)
    if ws is None:
        return

    created = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    row = [created, full_name or "", phone or "", comment or "", media_url or ""]
    try:
        ws.insert_row(row, index=2)
        logger.info("[GSHEET] inserted media row with URL at top (row 2)")
    except Exception as e:
        logger.error("[GSHEET] insert media row error: %s", e)


def gsheet_append_event(event: str, *, full_name: str = "", phone: str = ""):
    """
    Лог подій (кнопка менеджера, сервіс, кабель тощо) – у ту ж таблицю або іншу,
    як у тебе було раніше.
    """
    ws = _open_sheet_by_name(GSHEET_NAME)
    if ws is None:
        return

    created = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    row = [created, full_name, phone, event]
    try:
        ws.insert_row(row, index=2)
        logger.info("[GSHEET] inserted event row at top (row 2)")
    except Exception as e:
        logger.error("[GSHEET] insert event row error: %s", e)


# ===== НОВІ ФУНКЦІЇ (окрема медіа-таблиця, якщо захочеш) =====
def _open_media_sheet():
    """
    Відкриває окрему таблицю для медіа, якщо GSHEET_MEDIA_NAME задано.
    """
    if not GSHEET_MEDIA_NAME:
        return None
    ws = _open_sheet_by_name(GSHEET_MEDIA_NAME)
    return ws


def gsheet_append_media_row(
    *,
    context_name: str,
    full_name: str,
    phone: str,
    description: str,
    photo_links: Optional[List[str]] = None,
):
    """
    Записуємо рядок у таблицю медіа (опційно).

    context_name – 'service' / 'cable' / 'default'
    description  – короткий опис проблеми / заявки
    photo_links  – список URL-ів з Google Drive (може бути пустим)
    """
    ws = _open_media_sheet()
    if ws is None:
        # Якщо таблиці немає – мовчки нічого не робимо, щоб не ломати логіку.
        logger.debug("[GSHEET-MEDIA] media sheet not configured – skip")
        return

    created = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    photos_str = ", ".join(photo_links or [])

    row = [created, context_name, full_name, phone, description, photos_str]

    try:
        ws.insert_row(row, index=2)
        logger.info(
            "[GSHEET-MEDIA] inserted media row (%s, photos=%d)",
            context_name,
            len(photo_links or []),
        )
    except Exception as e:
        logger.error("[GSHEET-MEDIA] insert row error: %s", e)


def _open_catalog_sheet(sheet_title: str):
    """
    Відкриває вкладку з каталогу 'Асортимент, ціни та опис'
    за ID файлу (GSHEET_CATALOG_ID) і назвою листа.
    """
    if not GSHEET_CATALOG_ID:
        logger.debug("[CATALOG] GSHEET_CATALOG_ID not set – skip")
        return None

    client = _get_client()
    if client is None:
        return None

    try:
        sh = client.open_by_key(GSHEET_CATALOG_ID)
        ws = sh.worksheet(sheet_title)
        return ws
    except Exception as e:
        logger.error("[CATALOG] open sheet '%s' error: %s", sheet_title, e)
        return None


def load_cable_and_connector_types() -> List[dict]:
    """
    Зчитує каталог з файлу 'Асортимент, ціни та опис'
    (вкладки 'Кабелі' та 'Роз'єми') і повертає список словників:

    {
        "category": "cable" / "connector",
        "old_name": "...",
        "name": "...",
        "code": "...",
        "description": "...",
    }
    """
    items: List[dict] = []

    sheets_and_categories = [
        (GSHEET_CATALOG_SHEET_CABLES, "cable"),
        (GSHEET_CATALOG_SHEET_CONNECTORS, "connector"),
    ]

    for sheet_title, category in sheets_and_categories:
        if not sheet_title:
            continue

        ws = _open_catalog_sheet(sheet_title)
        if ws is None:
            continue

        try:
            rows = ws.get_all_values()
        except Exception as e:
            logger.error(
                "[CATALOG] get_all_values for '%s' error: %s",
                sheet_title,
                e,
            )
            continue

        if not rows:
            continue

        # структура:
        # A: стара назва
        # B: назва
        # C: код
        # D: фото (ігноруємо)
        # E: опис
        for row in rows[1:]:  # пропускаємо шапку
            if not any(cell.strip() for cell in row):
                continue

            old_name = row[0].strip() if len(row) > 0 else ""
            name = row[1].strip() if len(row) > 1 else ""
            code = row[2].strip() if len(row) > 2 else ""
            description = row[4].strip() if len(row) > 4 else ""

            if not name and not code:
                continue

            items.append(
                {
                    "category": category,
                    "old_name": old_name,
                    "name": name,
                    "code": code,
                    "description": description,
                }
            )

    logger.info("[CATALOG] loaded %d cable/connector items", len(items))
    return items
