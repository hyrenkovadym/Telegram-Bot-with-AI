# bot_core/service_ai.py
"""
AI-аналіз фото для сервісу.

Ідея:
- беремо до 3 фото + текстовий опис від клієнта;
- відправляємо в GPT як мульти-модальний запит (text + image);
- отримуємо відповідь з ймовірними причинами та кроками перевірки.

ВАЖЛИВО:
- це НЕ фінальний діагноз, а лише попередня оцінка;
- у відповіді ШІ завжди має бути явне застереження.
"""

from typing import List, Optional
import base64

from .config import MODEL_CHAT, OPENAI_CLIENT, FREE_MODE
from .logging_setup import logger
from .gpt_helpers import clean_plain_text


def _encode_image_to_data_url(img_bytes: bytes) -> str:
    """
    Конвертуємо байти зображення в data URL для vision-моделі OpenAI.
    """
    b64 = base64.b64encode(img_bytes).decode("ascii")
    return f"data:image/jpeg;base64,{b64}"


def analyze_service_case(
    comment_text: str,
    images: List[bytes],
) -> Optional[str]:
    """
    Аналізує сервісний кейс:
    - comment_text — опис проблеми від клієнта;
    - images — список фото (байти JPEG/PNG), ми візьмемо перші 3.

    Повертає готовий текст відповіді або None, якщо аналіз не вдався.
    """
    if FREE_MODE or OPENAI_CLIENT is None:
        logger.warning("[SERVICE-AI] FREE_MODE увімкнено або OPENAI_CLIENT is None – пропускаю аналіз.")
        return None

    if not images and not (comment_text or "").strip():
        logger.info("[SERVICE-AI] Немає ні фото, ні тексту – нічого аналізувати.")
        return None

    # Готуємо контент для користувацького повідомлення (text + image_url)
    user_content = []

    intro_text = (
        "Ти — сервісний інженер компанії FRENDT.\n"
        "Клієнт надіслав опис проблеми та фото техніки/обладнання.\n\n"
        "ВАЖЛИВО:\n"
        "- Твоя відповідь не є фінальним діагнозом, це лише ймовірна оцінка.\n"
        "- Завжди прямо наголошуй, що потрібна додаткова перевірка і, за потреби, виїзд інженера.\n\n"
        "Ось опис від клієнта (може бути порожнім):\n"
        f"{comment_text or '(опис відсутній)'}\n\n"
        "На основі цього опису та фото:\n"
        "1) Коротко опиши, що ти бачиш на фото (1–3 речення).\n"
        "2) Запропонуй 2–5 можливих причин проблеми.\n"
        "3) Дай 3–6 практичних кроків, що клієнт може сам перевірити.\n"
        "4) Обов'язково додай окремий абзац, де напишеш, що це НЕ 100% діагноз, а лише попередня оцінка.\n"
    )

    user_content.append({"type": "text", "text": intro_text})

    # Додаємо до 3 фото
    for img_bytes in images[:3]:
        try:
            data_url = _encode_image_to_data_url(img_bytes)
            user_content.append(
                {
                    "type": "image_url",
                    "image_url": {
                        "url": data_url,
                    },
                }
            )
        except Exception as e:
            logger.error("[SERVICE-AI] Помилка кодування зображення: %s", e)

    messages = [
        {
            "role": "system",
            "content": (
                "Ти — сервісний інженер FRENDT, який аналізує фото техніки та опис проблеми.\n"
                "Відповідай українською, структуровано, без зайвої води.\n"
                "Ніколи не подавай відповідь як остаточний діагноз — лише як ймовірні причини "
                "та рекомендації, що перевірити.\n"
            ),
        },
        {
            "role": "user",
            "content": user_content,
        },
    ]

    try:
        kwargs = {
            "model": MODEL_CHAT,
            "messages": messages,
        }

        # Узгодимося зі стилем core.py
        model_id = str(MODEL_CHAT or "")
        if model_id.startswith("gpt-5"):
            kwargs["max_completion_tokens"] = 500
        else:
            kwargs["max_tokens"] = 600
            kwargs["temperature"] = 0.3

        logger.info("[SERVICE-AI] Викликаю модель %s для сервісного аналізу.", MODEL_CHAT)
        resp = OPENAI_CLIENT.chat.completions.create(**kwargs)

        raw = resp.choices[0].message.content or ""
        logger.info("[SERVICE-AI] RAW відповідь моделі: %r", raw)

        text = clean_plain_text(raw).strip()
        if not text:
            logger.warning("[SERVICE-AI] Порожня відповідь після очищення.")
            return None

        return text
    except Exception as e:
        logger.error("[SERVICE-AI] Помилка під час запиту до OpenAI: %s", e)
        return None


