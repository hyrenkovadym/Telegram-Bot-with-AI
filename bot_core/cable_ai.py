# bot_core/cable_ai.py
"""
Модуль для розпізнавання типу кабелю / роз'єму по фото.

- тягне довідник з окремої таблиці Google Sheets (Асортимент, ціни та опис)
- надсилає фото + список позицій у мультимодальну модель OpenAI
- повертає найбільш ймовірний тип кабелю / роз'єму
"""

import base64
import os
from typing import Dict, List, Optional

from .config import OPENAI_CLIENT, MODEL_CHAT
from .logging_setup import logger
from .gsheets import load_cable_and_connector_types

# Окрема модель для аналізу фото (можна задати через .env)
# Наприклад: OPENAI_CABLE_MODEL=gpt-4o-mini або gpt-4.1-mini
CABLE_MODEL = os.getenv("OPENAI_CABLE_MODEL", MODEL_CHAT)


def _build_catalog_prompt(
    catalog_items: List[Dict],
    flow: Optional[str],
) -> str:
    """
    Формує текст з переліком кабелів/роз'ємів для підказки моделі.
    Очікуємо елементи у форматі:
        {
            "category": "cable" / "connector",
            "old_name": str,
            "name": str,
            "code": str,
            "description": str,
        }
    """
    if not catalog_items:
        return (
            "Список кабелів та роз'ємів порожній. "
            "Якщо можеш, опиши приблизний тип кабелю своїми словами."
        )

    if flow == "cable":
        header = (
            "Нижче наведено каталог кабелів і роз'ємів FRENDT.\n"
            "Спочатку спробуй підібрати КАБЕЛЬ, але якщо на фото видно лише роз'єм – "
            "допускається й варіант роз'єму.\n\n"
        )
    else:
        header = "Нижче наведено каталог кабелів і роз'ємів FRENDT.\n\n"

    lines: List[str] = []

    for item in catalog_items:
        category = item.get("category") or "item"
        name = (item.get("name") or "").strip()
        code = (item.get("code") or "").strip()
        desc = (item.get("description") or "").strip()

        if not name and not code:
            continue

        parts: List[str] = [f"[{category}]"]
        if code:
            parts.append(f"код: {code}")
        if name:
            parts.append(f"назва: {name}")
        if desc:
            parts.append(f"опис: {desc}")

        lines.append(" | ".join(parts))

    if not lines:
        return (
            "Каталог кабелів/роз'ємів не містить валідних рядків. "
            "Якщо можеш, опиши приблизний тип кабелю своїми словами."
        )

    return header + "\n".join(lines)


def _build_system_instruction() -> str:
    """
    Інструкція для моделі, як відповідати.
    """
    return (
        "Ти технічний експерт компанії FRENDT з кабельної продукції.\n"
        "Тобі надіслано фото кабелю або роз'єму та перелік усіх можливих типів.\n"
        "Твоє завдання — обрати ТІЛЬКИ ОДИН варіант, який найбільше відповідає фото.\n\n"
        "Правила відповіді:\n"
        "1. Якщо ти впевнений хоча б на ~60%, вибери найбільш ймовірний варіант.\n"
        "2. Якщо впевненість дуже низька (кабель не з каталогу, фото погане, не видно деталей) — "
        "відповідай рівно 'не впевнений'.\n"
        "3. Якщо можеш ідентифікувати, відповідай СТРОГО в одному рядку у форматі:\n"
        "   КОД | НАЗВА\n"
        "   Наприклад: 6518 | Кабель живлення i FullData (блок MTG)\n"
        "4. Не додавай ніяких пояснень, коментарів чи додаткового тексту."
    )


def _encode_image_to_data_url(image_bytes: bytes) -> str:
    """
    Кодує фото у data URL для image_url (OpenAI chat.completions).
    """
    b64 = base64.b64encode(image_bytes).decode("ascii")
    return f"data:image/jpeg;base64,{b64}"


def _parse_model_answer(text: str) -> Optional[Dict[str, str]]:
    """
    Розбираємо відповідь моделі. Очікуваний формат:
        'КОД | НАЗВА'
    або рівно 'не впевнений' / 'Не впевнений'.

    Повертаємо dict {'code': ..., 'name': ...} або None.
    """
    if not text:
        return None

    t = text.strip()
    low = t.lower()

    if low == "не впевнений" or low == "не впевнена":
        return {"code": "", "name": "не впевнений"}

    # Пробуємо поділити по '|'
    parts = [p.strip() for p in t.split("|")]
    if len(parts) == 1:
        # Модель могла повернути тільки назву без коду
        return {"code": "", "name": parts[0]}

    code = parts[0]
    name = parts[1]
    return {"code": code, "name": name}


# Кеш каталогу, щоб не читати Google Sheets кожен раз
_CATALOG_CACHE: Optional[List[Dict]] = None


def _get_catalog_items() -> List[Dict]:
    """
    Ледачий кеш для каталогу кабелів/роз'ємів.
    Якщо хочеш форс-оновлення – можна буде додати окрему команду /reload_catalog.
    """
    global _CATALOG_CACHE
    if _CATALOG_CACHE is not None:
        return _CATALOG_CACHE

    items = load_cable_and_connector_types()
    _CATALOG_CACHE = items
    return items


def reload_catalog_cache() -> int:
    """
    Можна викликати з адмін-команди, щоб примусово оновити кеш каталогу.
    Повертає кількість позицій.
    """
    global _CATALOG_CACHE
    items = load_cable_and_connector_types()
    _CATALOG_CACHE = items
    return len(items)


async def classify_cable_or_connector_from_photo(
    image_bytes: bytes,
    flow: Optional[str] = "cable",
) -> Optional[Dict[str, str]]:
    """
    Головна функція: надсилає фото + каталог в OpenAI і повертає:
        {'code': ..., 'name': ...}
    або {'name': 'не впевнений'}
    або None, якщо щось пішло не так.

    Поки що ця функція НЕ ВИКЛИКАЄТЬСЯ з хендлерів – інтегруємо окремо.
    """
    if OPENAI_CLIENT is None:
        logger.warning("[CABLE-AI] OPENAI_CLIENT is None, skip vision")
        return None

    if not image_bytes:
        logger.warning("[CABLE-AI] empty image_bytes")
        return None

    catalog_items = _get_catalog_items()
    if not catalog_items:
        logger.warning("[CABLE-AI] empty catalog_items – nothing to classify against")
        return None

    system_msg = _build_system_instruction()
    catalog_prompt = _build_catalog_prompt(catalog_items, flow)
    image_url = _encode_image_to_data_url(image_bytes)

    try:
        response = OPENAI_CLIENT.chat.completions.create(
            model=CABLE_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": system_msg,
                },
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": catalog_prompt},
                        {
                            "type": "image_url",
                            "image_url": {"url": image_url},
                        },
                    ],
                },
            ],
            max_completion_tokens=120,
        )
    except Exception as e:
        logger.error("[CABLE-AI] OpenAI error: %s", e)
        return None

    try:
        raw = response.choices[0].message.content or ""
    except Exception:
        raw = ""

    logger.info("[CABLE-AI] RAW answer: %r", raw)

    parsed = _parse_model_answer(raw)
    if not parsed:
        return None

    return parsed
