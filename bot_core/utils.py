# bot_core/utils.py
import os
import re
import time
import math
import json
from contextlib import suppress
from typing import Set, Iterable, List, Dict, Any

import openai

from telegram import Update
from telegram.ext import ContextTypes

from .config import (
    OPENAI_API_KEY,
    FREE_MODE,
    KB_DIR,
    KB_INDEX_PATH,
    BLACKLIST_FILE,
    SESSION_TIMEOUT_SEC,
    USE_WEB,
    F_PHONE,
    F_SITE,
)
from .logging_setup import logger

# ======== PDF reader ========
try:
    from pypdf import PdfReader
except Exception:
    PdfReader = None

# ======== HTML fetch (DuckDuckGo) ========
try:
    import requests
    from bs4 import BeautifulSoup
except Exception:
    requests = None
    BeautifulSoup = None

# ======== OpenAI client for embeddings ========
_EMBED_CLIENT = None
if not FREE_MODE and OPENAI_API_KEY:
    _EMBED_CLIENT = openai.OpenAI(api_key=OPENAI_API_KEY)


# ========= PHONE & TEXT UTILS =========
def normalize_phone(phone_raw: str) -> str:
    """
    Жорстка нормалізація номера: лишаємо тільки цифри + можливий плюс.
    """
    if not phone_raw:
        return ""
    s = phone_raw.strip()
    plus = "+" if s.startswith("+") else ""
    digits = re.sub(r"\D", "", s)
    return (plus + digits) if digits else ""


def try_normalize_user_phone(text: str) -> str | None:
    """
    "Розумна" нормалізація телефонів з тексту:
    - +380XXXXXXXXX
    - 0XXXXXXXXX → +380XXXXXXXXX
    - 380XXXXXXXXX → +380XXXXXXXXX
    - 12 цифр → +XXXXXXXXXXXX
    """
    if not text:
        return None
    s = text.strip()
    has_plus = s.startswith("+")
    digits = re.sub(r"\D", "", s)
    if not digits:
        return None

    if has_plus:
        return "+" + digits
    if digits.startswith("0") and len(digits) == 10:
        return "+380" + digits[1:]
    if digits.startswith("380"):
        return "+" + digits
    if len(digits) == 12:
        return "+" + digits
    return None


def contains_emoji(text: str) -> bool:
    if not text:
        return False
    return bool(
        re.compile(
            "["  # діапазони emoji
            "\U0001F600-\U0001F64F"
            "\U0001F300-\U0001F5FF"
            "\U0001F680-\U0001F6FF"
            "\U0001F1E0-\U0001F1FF"
            "]+",
            flags=re.UNICODE,
        ).search(text)
    )


# ========= BLACKLIST / STAFF NUMBERS =========
# один спільний кеш для "спец"-номерів (у т.ч. співробітники)
_BLACKLIST_NORMALIZED: Set[str] = set()


def _iter_blacklist_lines(path: str) -> Iterable[str]:
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            raw = line.strip()
            if not raw or raw.startswith("#"):
                continue
            yield raw


def reload_blacklist() -> int:
    """
    Перечитує blacklist_phones.txt та зберігає нормалізовані номери.
    Використовуємо як для "не дзвонити", так і для спец-номерів (співробітники).
    """
    global _BLACKLIST_NORMALIZED
    new_set: Set[str] = set()
    for raw in _iter_blacklist_lines(BLACKLIST_FILE):
        norm = normalize_phone(raw)
        if norm:
            new_set.add(norm)
    _BLACKLIST_NORMALIZED = new_set
    logger.info("Blacklist loaded: %s numbers", len(_BLACKLIST_NORMALIZED))
    return len(_BLACKLIST_NORMALIZED)


def is_blacklisted(phone: str) -> bool:
    """
    Чи є номер у списку спец-номерів/blacklist.
    """
    norm = normalize_phone(phone)
    return bool(norm) and (norm in _BLACKLIST_NORMALIZED)


def is_staff_phone(phone: str) -> bool:
    """
    Номер співробітника: зараз прирівнюємо до номерів із blacklist_phones.txt.
    Якщо захочеш — легко винесемо в окремий файл staff_phones.txt.
    """
    norm = normalize_phone(phone)
    return bool(norm) and (norm in _BLACKLIST_NORMALIZED)


# ========= DIALOG & SESSION MEMORY =========
def ensure_dialog(context: ContextTypes.DEFAULT_TYPE):
    ud = context.user_data
    ud.setdefault("dialog", [])
    ud.setdefault("last_time", time.time())
    ud.setdefault("phone", "")
    ud.setdefault("first_q_saved", False)
    return ud


def touch_session(context: ContextTypes.DEFAULT_TYPE):
    context.user_data["last_time"] = time.time()


def session_expired(context: ContextTypes.DEFAULT_TYPE) -> bool:
    last = context.user_data.get("last_time", 0)
    return (time.time() - last) > SESSION_TIMEOUT_SEC


def reset_session(context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    context.user_data.update(
        {
            "dialog": [],
            "last_time": time.time(),
            "phone": "",
            "first_q_saved": False,
        }
    )


def _now() -> float:
    return time.time()


def add_history(context: ContextTypes.DEFAULT_TYPE, role: str, content: str):
    ud = context.user_data
    hist = ud.setdefault("dialog", [])
    hist.append({"role": role, "content": content, "ts": _now()})
    cutoff = _now() - SESSION_TIMEOUT_SEC
    hist[:] = [t for t in hist if t.get("ts", _now()) >= cutoff]
    if len(hist) > 80:
        del hist[: len(hist) - 80]


def last_user_message(context: ContextTypes.DEFAULT_TYPE) -> str | None:
    for turn in reversed(context.user_data.get("dialog", [])):
        if turn.get("role") == "user":
            return turn.get("content", "")
    return None


# ========= SESSION (JobQueue) =========
def schedule_session_expiry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Плануємо завершення сесії через SESSION_TIMEOUT_SEC.
    """
    jq = context.job_queue
    if jq is None:
        logger.warning(
            'JobQueue відсутній. Встанови: pip install "python-telegram-bot[job-queue]"'
        )
        return

    chat_id = update.effective_chat.id
    old_job = context.chat_data.get("expiry_job")
    if old_job:
        with suppress(Exception):
            old_job.schedule_removal()

    job = jq.run_once(
        end_session_job,
        when=SESSION_TIMEOUT_SEC,
        chat_id=chat_id,
        name=f"expire_{chat_id}",
    )
    context.chat_data["expiry_job"] = job
    context.chat_data["last_time"] = time.time()


async def end_session_job(context: ContextTypes.DEFAULT_TYPE):
    """
    Викликається після тайм-ауту сесії.
    Чистимо chat_data та відправляємо кілька службових повідомлень.
    """
    chat_id = context.job.chat_id
    from .ui import bottom_keyboard, main_menu_keyboard  # локальний імпорт щоб уникнути циклів

    try:
        await context.bot.send_message(
            chat_id=chat_id,
            text="Дякую за запитання!",
            reply_markup=main_menu_keyboard(),
        )
        await context.bot.send_message(
            chat_id=chat_id,
            text=(
                "Підпишіться на наші соцмережі, щоб бути в курсі новин і корисних порад.\n"
                f"Телефон підтримки: {F_PHONE}\nВебсайт: https://{F_SITE}\n\n"
                "Я ваш помічник FRENDT. Задавайте своє питання 👇"
            ),
            reply_markup=main_menu_keyboard(),
        )
        await context.bot.send_message(
            chat_id=chat_id,
            text="Готовий допомогти.",
            reply_markup=bottom_keyboard(context, tg_user_id=str(chat_id)),
        )
    except Exception as e:
        logger.warning(
            "Не вдалося надіслати повідомлення про завершення сесії: %s", e
        )

    chat_data = context.application.chat_data.get(chat_id)
    if isinstance(chat_data, dict):
        chat_data.clear()


# ========= KB BUILDING & SEARCH =========
def _chunk_text(txt: str, chunk_size: int = 900, overlap: int = 120) -> List[str]:
    txt = re.sub(r"[ \t]+", " ", txt)
    txt = re.sub(r"\n{3,}", "\n\n", txt).strip()
    chunks = []
    i = 0
    while i < len(txt):
        chunk = txt[i : i + chunk_size]
        if i + chunk_size < len(txt):
            j = chunk.rfind(". ")
            if j > 300:
                chunk = chunk[: j + 1]
        chunks.append(chunk.strip())
        i += max(len(chunk) - overlap, 1)
    return [c for c in chunks if len(c) >= 120]


def _pdf_to_text(path: str) -> str:
    if PdfReader is None:
        logger.warning("пакет pypdf не встановлено — пропускаю PDF: %s", path)
        return ""
    try:
        reader = PdfReader(path)
        return "\n".join([(p.extract_text() or "") for p in reader.pages])
    except Exception as e:
        logger.error("[KB] PDF read fail %s: %s", path, e)
        return ""


def _txt_to_text(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except Exception as e:
        logger.error("[KB] TXT read fail %s: %s", path, e)
        return ""


def _embed_texts(texts: List[str]) -> List[List[float]]:
    if FREE_MODE or _EMBED_CLIENT is None:
        # у FREE_MODE ембеддинги не використовуємо
        return [[0.0] for _ in texts]
    resp = _EMBED_CLIENT.embeddings.create(
        model="text-embedding-3-small",
        input=texts,
    )
    return [d.embedding for d in resp.data]


def _cosine(a: List[float], b: List[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)) or 1e-8
    nb = math.sqrt(sum(y * y for y in b)) or 1e-8
    return dot / (na * nb)


def _tokenize_query(q: str) -> List[str]:
    """
    Розбиваємо запит на смислові токени:
    - все до нижнього регістру
    - залишаємо:
        * усі числа
        * слова від 4+ символів (укр/латиниця)
        * латинські абревіатури типу RTK, GPS (3+ символи)
    """
    q = q.lower()
    raw_tokens = re.findall(r"[a-zа-щьюяєіїґ0-9]+", q)

    tokens: List[str] = []
    for t in raw_tokens:
        if t.isdigit():
            tokens.append(t)
        elif len(t) >= 4:
            tokens.append(t)
        elif len(t) >= 3 and re.match(r"[a-z0-9]+$", t):
            tokens.append(t)
    return tokens


def kb_build_or_load() -> Dict[str, Any]:
    """
    Будує або завантажує індекс бази знань із KB_DIR у KB_INDEX_PATH.
    """
    os.makedirs(KB_DIR, exist_ok=True)

    if FREE_MODE:
        logger.info("[KB] FREE_MODE: індексація без OpenAI (тільки текстовий пошук).")

    # пробуємо прочитати існуючий індекс
    if os.path.exists(KB_INDEX_PATH):
        try:
            with open(KB_INDEX_PATH, "r", encoding="utf-8") as f:
                idx = json.load(f)
            files_now = []
            for fn in os.listdir(KB_DIR):
                if fn.lower().endswith((".txt", ".pdf")):
                    path = os.path.join(KB_DIR, fn)
                    files_now.append(
                        {"path": path, "mtime": os.path.getmtime(path)}
                    )
            old = {
                (d["path"], round(d.get("mtime", 0), 6))
                for d in idx.get("files", [])
            }
            cur = {
                (d["path"], round(d.get("mtime", 0), 6))
                for d in files_now
            }
            if old == cur and idx.get("chunks"):
                logger.info("[KB] Завантажено індекс: %s", KB_INDEX_PATH)
                return idx
        except Exception as e:
            logger.warning("[KB] Неможливо прочитати індекс (%s). Перебудовую…", e)

    pdf_paths = [
        os.path.join(KB_DIR, fn)
        for fn in os.listdir(KB_DIR)
        if fn.lower().endswith(".pdf")
    ]
    txt_paths = [
        os.path.join(KB_DIR, fn)
        for fn in os.listdir(KB_DIR)
        if fn.lower().endswith(".txt")
    ]

    all_chunks: List[Dict[str, Any]] = []
    for path in txt_paths:
        txt = _txt_to_text(path)
        for i, ch in enumerate(_chunk_text(txt)):
            all_chunks.append(
                {
                    "text": ch,
                    "source": os.path.basename(path),
                    "i": i,
                    "type": "txt",
                }
            )

    for path in pdf_paths:
        txt = _pdf_to_text(path)
        if not txt:
            continue
        for i, ch in enumerate(_chunk_text(txt)):
            all_chunks.append(
                {
                    "text": ch,
                    "source": os.path.basename(path),
                    "i": i,
                    "type": "pdf",
                }
            )

    if not all_chunks:
        logger.warning("[KB] Порожній контент. Поклади .txt або .pdf у %s", KB_DIR)
        return {"model": "text-embedding-3-small", "files": [], "chunks": []}

    embeds = _embed_texts([c["text"] for c in all_chunks])
    for c, emb in zip(all_chunks, embeds):
        c["embedding"] = emb

    files_meta = []
    for fn in os.listdir(KB_DIR):
        if fn.lower().endswith((".txt", ".pdf")):
            p = os.path.join(KB_DIR, fn)
            files_meta.append({"path": p, "mtime": os.path.getmtime(p)})

    idx = {
        "model": "text-embedding-3-small",
        "files": files_meta,
        "chunks": all_chunks,
    }
    try:
        with open(KB_INDEX_PATH, "w", encoding="utf-8") as f:
            json.dump(idx, f, ensure_ascii=False)
        logger.info("[KB] Побудовано індекс із %d фрагментів.", len(all_chunks))
    except Exception as e:
        logger.warning("[KB] Не вдалося зберегти індекс: %s", e)
    return idx


_KB_INDEX: Dict[str, Any] = {}


def kb_retrieve_smart(query: str, k: int = 6) -> List[Dict[str, Any]]:
    """
    Розумний пошук по базі знань:
    1) Спочатку ранжуємо фрагменти за кількістю збігів токенів.
    2) Якщо є збіги — повертаємо топ-k + ще 1–2 фрагменти з найвищою семантичною
       схожістю.
    3) Якщо буквальних збігів немає — використовуємо тільки ембеддинги.
    """
    if not _KB_INDEX or not _KB_INDEX.get("chunks"):
        return []

    tokens = _tokenize_query(query)
    chunks = _KB_INDEX["chunks"]

    literal_scored: List[tuple[int, Dict[str, Any]]] = []

    if tokens:
        for ch in chunks:
            t = ch["text"].lower()
            hit_count = sum(1 for tok in tokens if tok in t)
            if hit_count > 0:
                literal_scored.append((hit_count, ch))

    if literal_scored:
        literal_scored.sort(key=lambda x: x[0], reverse=True)
        top_literal = [c for _, c in literal_scored[:k]]

        # додаємо 1–2 найкращі семантичні збіги
        try:
            q_emb = _embed_texts([query])[0]
        except Exception:
            return top_literal

        scored_sem: List[tuple[float, Dict[str, Any]]] = []
        for ch in chunks:
            sim = _cosine(q_emb, ch.get("embedding", [0.0]))
            scored_sem.append((sim, ch))
        scored_sem.sort(key=lambda x: x[0], reverse=True)

        extra: List[Dict[str, Any]] = []
        for _, ch in scored_sem:
            if ch not in top_literal:
                extra.append(ch)
            if len(extra) >= 2:
                break

        return top_literal + extra

    # якщо буквальних збігів немає — лише ембеддинги
    q_emb = _embed_texts([query])[0]
    scored_sem = []
    for ch in chunks:
        sim = _cosine(q_emb, ch.get("embedding", [0.0]))
        scored_sem.append((sim, ch))
    scored_sem.sort(key=lambda x: x[0], reverse=True)
    return [c for _, c in scored_sem[:k]]


def pack_snippets(snips: List[Dict[str, Any]], max_chars: int = 5000) -> str:
    """
    Пакує кілька фрагментів KB у один текстовий блок для системного повідомлення.
    """
    out: List[str] = []
    total = 0
    for s in snips:
        tag = f"[{s['source']} • {s['i']}]"
        block = f"{tag}\n{s['text'].strip()}"
        if total + len(block) > max_chars and out:
            break
        out.append(block)
        total += len(block)
    return "\n\n---\n\n".join(out)


# ========= WEB FALLBACK =========
def fetch_url(url: str, timeout: float = 8.0) -> str:
    if requests is None:
        return ""
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
            "Accept-Language": "uk,ru;q=0.9,en;q=0.8",
        }
        r = requests.get(url, headers=headers, timeout=timeout)
        if r.status_code == 200 and r.text:
            return r.text
    except Exception as e:
        logger.debug("fetch_url error %s: %s", url, e)
    return ""


def duckduckgo_search(query: str, n: int = 3) -> List[str]:
    if requests is None or BeautifulSoup is None:
        return []
    q = query.strip()
    url = f"https://duckduckgo.com/html/?q={requests.utils.quote(q)}&kl=ua-uk&kp=1"
    html = fetch_url(url)
    if not html:
        return []
    soup = BeautifulSoup(html, "html.parser")
    links: List[str] = []
    for a in soup.select("a.result__a"):
        href = a.get("href")
        if href and href.startswith("http") and "duckduckgo.com" not in href:
            links.append(href)
        if len(links) >= n:
            break
    return links


def extract_text_from_html(html: str, max_chars: int = 4000) -> str:
    if BeautifulSoup is None:
        return ""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    text = soup.get_text("\n")
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]+", " ", text)
    return text.strip()[:max_chars]


def build_web_context(query: str, max_pages: int = 3) -> str:
    """
    Формує текстовий "контекст із вебу" для GPT на основі DuckDuckGo.
    """
    if FREE_MODE or not USE_WEB:
        return ""
    urls = duckduckgo_search(query, n=max_pages)
    chunks: List[str] = []
    for u in urls:
        html = fetch_url(u)
        if not html:
            continue
        plain = extract_text_from_html(html)
        if plain:
            chunks.append(f"[{u}]\n{plain}")
    return "\n\n---\n\n".join(chunks)[:6000]


def clean_plain_text(s: str) -> str:
    """
    Прибирає Markdown-виділення (*...*, _..._, `...`) і зайві переводи рядків.
    Використовується для очистки відповіді GPT перед відправкою в Telegram.
    """
    if not s:
        return s
    s = re.sub(r"\*{1,3}([^*\n]+)\*{1,3}", r"\1", s)
    s = re.sub(r"_{1,3}([^_\n]+)_{1,3}", r"\1", s)
    s = re.sub(r"`{1,3}", "", s)
    s = re.sub(r"\r\n", "\n", s).strip()
    return s

# ========= LONG TELEGRAM MESSAGES =========
async def send_long_reply(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    text: str,
    reply_markup=None,
    chunk_size: int = 3500,
):
    """
    Надсилає довге повідомлення в кілька меседжів, якщо воно перевищує ліміт.

    - Корисно для відповідей GPT та сервісного AI, щоб не впиратися у ліміт Telegram (~4096 символів).
    - Перша частина отримує reply_markup (клавіатуру), наступні — без неї.
    """
    if not text:
        return

    s = str(text).strip()
    if not s:
        return

    parts: List[str] = []
    while len(s) > chunk_size:
        # Прагнемо різати по "красивих" межах
        cut = s.rfind("\n\n", 0, chunk_size)
        if cut == -1:
            cut = s.rfind("\n", 0, chunk_size)
        if cut == -1:
            cut = s.rfind(". ", 0, chunk_size)
        if cut == -1:
            cut = chunk_size

        chunk = s[:cut].strip()
        if chunk:
            parts.append(chunk)
        s = s[cut:].lstrip()

    if s.strip():
        parts.append(s.strip())

    for i, part in enumerate(parts):
        try:
            if i == 0:
                await update.message.reply_text(part, reply_markup=reply_markup)
            else:
                await update.message.reply_text(part)
        except Exception as e:
            logger.error("send_long_reply error: %s", e)
            break
