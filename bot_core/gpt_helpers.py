# bot_core/gpt_helpers.py
from typing import List, Dict, Any

from .config import F_COMPANY, F_SITE, F_PHONE, OPENAI_CLIENT
from .logging_setup import logger
from .utils import clean_plain_text as _clean_plain_text


def clean_plain_text(s: str) -> str:
    """
    Обгортка, щоб core/staff могли імпортувати clean_plain_text
    з цього модуля, але сама логіка живе в utils.
    """
    return _clean_plain_text(s)


# ====== БАЗОВІ СИСТЕМНІ ПРОМПТИ ======

BASE_SYSTEM_PROMPT = f"""
Ти — ШІ-помічник компанії {F_COMPANY}, українського Центру точного землеробства.

Мова:
- Відповідай тільки українською.
- Пиши просто й по-людськи, без зайвої канцелярщини.

Стиль відповіді (розгорнуто, але без води):
- Спочатку 1–2 речення: короткий висновок/рішення.
- Далі структуровано: 6–12 пунктів (маркерований список) з конкретикою:
  що перевірити, що зробити, які дані уточнити, які ризики/обмеження.
- Якщо є кілька варіантів — дай 2 варіанти максимум і поясни «коли який».
- Якщо потрібні уточнення — задай 3–6 точних запитань наприкінці.
- Не пиши загальних “порад”, не повторюй запит, не роби довгих вступів.
- Обсяг зазвичай 120–250 слів (можна більше, якщо це сервіс/кабелі і потрібні кроки).

Пріоритети:
- Якщо питання про автопілоти, навігацію, RTK, агрохімію, сервіс чи кабельні жгути —
  у першу чергу пропонуй рішення FRENDT:
  автопілоти TerraNavix, Hexagon, CHCNAV, Ag Leader (SteadySteer, SteerCommand Z2), мережу FarmRTK, сервіс FRENDT.
- Не роби «огляд ринку». Пропонуй 1–2 конкретні варіанти під задачу клієнта, з коротким поясненням «чому саме це».

Контакти:
- Офіційний сайт: https://{F_SITE}
- Телефон підтримки: {F_PHONE}
Використовуй контакти лише там, де це логічно (коли клієнт просить, куди звернутись далі),
а не в кожній відповіді.

Обмеження:
- Не вигадуй внутрішні деталі компанії, яких немає в базі знань чи тексті користувача.
- Не називай точний номер або версію GPT. Якщо питають «яка в тебе версія» —
  відповідай, що ти мовна модель OpenAI, адаптована під задачі FRENDT, без уточнення цифр.
- Якщо користувач не питає «хто ви», не починай відповідь з опису компанії — одразу переходь до суті питання.

Інструкції/мануали:
- Якщо користувач просить: «скинь інструкцію», «мануал», «pdf», «інструкцію по встановленню/налаштуванню» —
  НЕ відповідай, що «не можеш скинути файл» або що «інструкції немає».
- Замість цього дай «інструкцію в тексті» максимально практично:
  1) Підготовка (що потрібно мати, що перевірити).
  2) Монтаж (антена/дисплей/мотор керма/живлення/прокладка кабелів).
  3) Перший запуск (налаштування профілю техніки, виміри, калібрування).
  4) RTK (SIM/інтернет/підключення, що має показувати).
  5) Типові помилки і що робити.
- Якщо в KB є конкретні кроки/пункти — використовуй їх і пиши покроково, без води.
- Наприкінці попроси 2–4 уточнення (модель техніки, модель термінала, тип керма/гідравліки, чи є RTK).
""".strip()



BASE_STAFF_SYSTEM_PROMPT = """
Ти — внутрішній ШІ-помічник для співробітників компанії FRENDT.

Мова й стиль:
- Відповідай українською (технічні терміни можна англійською).
- Будь конкретним, структурованим і лаконічним.

Що дозволено:
- Допомагати з текстами для клієнтів (скрипти дзвінків, повідомлення, листи, описи товарів і послуг).
- Пояснювати технічні речі (автопілоти, RTK, навігація, кабелі, сервіс, агрохімія).
- Давати ідеї для маркетингу, сценаріїв бота, обробки лідів, внутрішніх процесів.
- Допомагати з кодом, структурами проєктів, технічними документами.


""".strip()


# ====== ПІДКАЗКА ПРО ПОТОЧНИЙ РОЗДІЛ / СЦЕНАРІЙ ======


def _build_section_hint(context) -> str:
    """
    Формує текст-підказку для GPT про поточний розділ меню / сценарій.
    """
    ud = context.user_data or {}
    section = ud.get("section")
    flow = ud.get("flow")  # service / cable / інші сценарії

    # Окремий режим: Загальні питання (без агро-фокусу)
    if section == "global":
        return (
            "Користувач знаходиться в розділі «Загальні питання».\n"
            "Відповідай як універсальний асистент на будь-які теми, "
            "а не лише про агро чи продукти FRENDT.\n"
            "Не нав'язуй FRENDT, якщо користувач сам прямо про це не питає.\n"
        )

    parts: List[str] = []

    if section == "autopilot":
        parts.append(
            "Зараз користувач знаходиться в розділі «Автопілот» — підбір автопілотів "
            "та рішень автоматичного водіння для тракторів і комбайнів."
        )
    elif section == "navigation":
        parts.append(
            "Зараз користувач знаходиться в розділі «Навігація» — паралельне водіння, "
            "моніторинг робіт, карти полів та робота з навігаційними терміналами."
        )
    elif section == "seeder":
        parts.append(
            "Зараз користувач знаходиться в розділі «Переобладнання обприскувача» — "
            "модернізація обприскувачів, установка електроніки, секційний контроль, норми внесення тощо."
        )
    elif section == "agrochem":
        parts.append(
            "Зараз користувач знаходиться в розділі «Агрохімічні дослідження» — "
            "аналіз ґрунту, карти забезпеченості елементами, рекомендації та VRA-внесення."
        )
    elif section == "rtk":
        parts.append(
            "Зараз користувач знаходиться в розділі «RTK-станції» — мережа FarmRTK, "
            "покриття сигналом, підключення техніки, точність 2–3 см."
        )
    elif section == "agroconsult":
        parts.append(
            "Зараз користувач знаходиться в розділі «Агрономічний консалтинг» — "
            "агрономічні поради, технологічний супровід і оптимізація витрат."
        )
    elif section == "cables":
        parts.append(
            "Зараз користувач знаходиться в розділі «Кабельна продукція» — виготовлення "
            "та ремонт проводок, кабельних жгутів і штекерів для агроелектроніки."
        )
    elif section == "service":
        parts.append(
            "Зараз користувач знаходиться в розділі «Сервіс» — діагностика та ремонт "
            "обладнання, виїзд інженерів, підтримка клієнтів."
        )

    if flow == "service":
        parts.append(
            "Активний сценарій «Сервіс»: користувач описує проблему з технікою/системою "
            "і може надсилати фото для діагностики."
        )
    elif flow == "cable":
        mode = ud.get("cable_mode")
        if mode == "make":
            parts.append(
                "Активний сценарій кабельної продукції: виготовлення нової проводки "
                "на основі фото та опису."
            )
        elif mode == "repair_own":
            parts.append(
                "Активний сценарій кабельної продукції: ремонт проводки з існуючими "
                "штекерами клієнта."
            )
        elif mode == "repair_frendt":
            parts.append(
                "Активний сценарій кабельної продукції: ремонт/виготовлення проводки "
                "з новими штекерами FRENDT."
            )

    if not parts:
        return ""

    # Головний смисл: при «що це?» пояснюємо розділ, а не всю компанію
    parts.append(
        "Якщо користувач пише щось типу «що це», «що за розділ», «а це що робить» — "
        "пояснюй саме поточний розділ/сервіс і пов'язані з ним послуги, а не опис компанії в цілому."
    )

    return " ".join(parts)


# ====== ФОРМУВАННЯ messages ДЛЯ КЛІЄНТІВ ======


def build_messages_for_openai(
    context,
    source_mode: str,
    last_user_text: str,
    kb_context: str | None = None,
    web_context: str | None = None,
) -> List[Dict[str, Any]]:
    """
    Формує messages для OPENAI_CLIENT.chat.completions.create(...)

    source_mode: "kb" | "web" | "plain"
    """
    ud = context.user_data or {}
    dialog = ud.get("dialog", [])

    section_hint = _build_section_hint(context)

    system_lines: List[str] = [BASE_SYSTEM_PROMPT]

    if section_hint:
        system_lines.append("Контекст розділу:\n" + section_hint)

    if source_mode == "kb":
        system_lines.append(
            "Ти відповідаєш на основі внутрішньої бази знань FRENDT. "
            "Спочатку спирайся на надані фрагменти, а потім, за потреби, додавай загальні пояснення."
        )
    elif source_mode == "web":
        system_lines.append(
            "Ти маєш додатковий контекст із публічних веб-джерел. Використовуй його обережно, "
            "надаючи пріоритет рішенню задачі клієнта і стилю FRENDT."
        )
    else:  # plain
        system_lines.append(
            "Відповідай, спираючись на попередній діалог та загальні знання, якщо база знань "
            "не дала прямого хіта."
        )

    # Додатковий наголос на лаконічності (дублюємо, щоб модель точно запам'ятала)
    system_lines.append(
        "Будь лаконічним: не більше кількох абзаців. Спершу дай відповідь по суті, "
        "а лише потім — додаткові пояснення, якщо вони дійсно потрібні."
    )

    system_prompt = "\n\n".join(system_lines)

    messages: List[Dict[str, Any]] = []
    messages.append({"role": "system", "content": system_prompt})

    # KB-контекст
    if kb_context:
        messages.append(
            {
                "role": "system",
                "content": (
                    "Нижче наведені витяги з внутрішньої бази знань FRENDT. "
                    "Посилайся на них, коли відповідаєш користувачу:\n\n" + kb_context
                ),
            }
        )

    # WEB-контекст
    if web_context:
        messages.append(
            {
                "role": "system",
                "content": (
                    "Нижче — текст із публічних веб-джерел, який може допомогти у відповіді. "
                    "Використовуй його як додатковий фон, якщо це доречно:\n\n"
                    + web_context
                ),
            }
        )

    # Історія діалогу (щоб GPT бачив, що ми вже казали «ви обрали розділ …»)
    history = dialog[-14:]
    for turn in history:
        role = turn.get("role") or "user"
        content = turn.get("content") or ""
        if not content:
            continue
        messages.append({"role": role, "content": content})

    # Поточне питання користувача
    messages.append({"role": "user", "content": last_user_text})

    return messages


# ====== ФОРМУВАННЯ messages ДЛЯ STAFF MODE ======


def build_messages_for_staff(context, user_message: str) -> List[Dict[str, Any]]:
    """
    Формує messages для режиму співробітника (staff_mode).
    Тут можна бути технічнішим, але без вигадування «внутрішніх» версій GPT і секретів.
    """
    ud = context.user_data or {}
    dialog = ud.get("dialog", [])

    system_lines: List[str] = [BASE_STAFF_SYSTEM_PROMPT]

    section_hint = _build_section_hint(context)
    if section_hint:
        system_lines.append(
            "Зараз співробітник працює в контексті певного розділу/сценарію бота. " + section_hint
        )

    # Трошки про стиль для staff
    system_lines.append(
        "Відповідай чітко і по суті. Якщо просили приклади текстів або скрипти — давай готові варіанти, "
        "які можна одразу копіювати в бот чи месенджер."
    )

    system_prompt = "\n\n".join(system_lines)

    messages: List[Dict[str, Any]] = []
    messages.append({"role": "system", "content": system_prompt})

    # Історія для staff (можна трохи більше)
    history = dialog[-20:]
    for turn in history:
        role = turn.get("role") or "user"
        content = turn.get("content") or ""
        if not content:
            continue
        messages.append({"role": role, "content": content})

    messages.append({"role": "user", "content": user_message})

    return messages


# ====== ЄДИНИЙ helper для виклику OpenAI з ретраями ======


def _extract_text_from_choice(choice) -> str:
    msg = choice.message
    raw = ""

    content = getattr(msg, "content", None)

    # 1) content як рядок
    if isinstance(content, str):
        raw = content

    # 2) content як список частин (parts)
    elif isinstance(content, list):
        parts: List[str] = []
        for part in content:
            # part може бути dict
            if isinstance(part, dict):
                if part.get("type") == "text":
                    t = (part.get("text") or "").strip()
                    if t:
                        parts.append(t)
                continue

            # або об'єкт
            if getattr(part, "type", None) == "text":
                t = (getattr(part, "text", "") or "").strip()
                if t:
                    parts.append(t)

        raw = "\n".join(parts)

    # 3) refusal fallback
    if not raw:
        refusal = getattr(msg, "refusal", None)
        if isinstance(refusal, str) and refusal.strip():
            raw = refusal.strip()

        if not raw:
            refusal2 = getattr(choice, "refusal", None)
            if isinstance(refusal2, str) and refusal2.strip():
                raw = refusal2.strip()

    return raw or ""



def openai_chat_with_retry(
    kwargs: Dict[str, Any],
    *,
    label: str,
    max_attempts: int = 1,
) -> str:
    """
    Викликає OPENAI_CLIENT.chat.completions.create(**kwargs) з кількома спробами.
    Повертає вже ОЧИЩЕНИЙ текст (clean_plain_text + strip) або порожній рядок,
    якщо усі спроби дали порожню відповідь / помилку.

    kwargs — це той самий dict, який раніше передавався в OPENAI_CLIENT.chat.completions.create.
    label — умовна назва (KB / PLAIN / STAFF) для логів.
    max_attempts — скільки разів максимум пробуємо.
    """
    if OPENAI_CLIENT is None:
        logger.error("openai_chat_with_retry(%s): OPENAI_CLIENT is None", label)
        return ""

    last_clean = ""

    for attempt in range(1, max_attempts + 1):
        try:
            response = OPENAI_CLIENT.chat.completions.create(**kwargs)
        except Exception as e:
            logger.error(
                "OpenAI %s error on attempt %d: %s",
                label,
                attempt,
                e,
            )
            continue

        model_name = getattr(response, "model", kwargs.get("model", "unknown"))
        raw = _extract_text_from_choice(response.choices[0])
        logger.info(
            "OpenAI %s model used: %s (attempt %d)",
            label,
            model_name,
            attempt,
        )
        logger.info("OpenAI %s RAW answer: %r", label, raw)

        clean = clean_plain_text(raw).strip()
        if clean:
            return clean

        logger.warning(
            "OpenAI %s empty answer from model on attempt %d",
            label,
            attempt,
        )
        last_clean = clean

    return last_clean

