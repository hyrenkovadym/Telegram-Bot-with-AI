# bot_core/config.py
import os
from datetime import timezone
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

# ========= ENV & GLOBALS =========

load_dotenv()

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
F_PHONE = os.getenv("SUPPORT_PHONE", "+380678120905").strip()

# ===== МОДЕЛІ =====
# Базове значення, якщо в .env нічого не задано
# (дешева модель за замовчуванням)
MODEL_CHAT_DEFAULT = "gpt-5.1"

# Модель для клієнтського режиму
MODEL_CHAT = (os.getenv("OPENAI_CHAT_MODEL", "") or MODEL_CHAT_DEFAULT).strip()

# Модель для режиму співробітника:
# за замовчуванням та ж, що й MODEL_CHAT,
# але можна окремо задати через OPENAI_STAFF_MODEL
MODEL_STAFF = (os.getenv("OPENAI_STAFF_MODEL", "") or MODEL_CHAT).strip()

USE_WEB = os.getenv("USE_WEB", "1") == "1"

KB_DIR = os.getenv("KB_DIR", "kb")
KB_INDEX_PATH = os.path.join(KB_DIR, os.getenv("KB_INDEX_PATH", "kb_index.json"))

FREE_MODE = (OPENAI_API_KEY == "")

PORT = int(os.getenv("PORT", "8080"))
WEBHOOK_BASE = os.getenv("WEBHOOK_BASE", "").rstrip("/")
WEBHOOK_PATH = os.getenv("WEBHOOK_PATH", "/telegram")
TELEGRAM_SECRET = os.getenv("TELEGRAM_SECRET", "secret")

DATABASE_URL = os.getenv("DATABASE_URL", "").strip()

# ========= CONSTS =========
SESSION_TIMEOUT_SEC = 15 * 60
F_COMPANY = "FRENDT"
F_SITE = "frendt.ua"

MANAGER_BTN = "Зв’язатись з менеджером"
MENU_BTN = "Меню"
STAFF_BTN = "Режим співробітника"
BACK_BTN = "Назад"

# Таймзона
try:
    TZ = ZoneInfo("Europe/Kyiv")
except Exception:
    TZ = timezone.utc

GSHEET_PATH = "frendt-service.json"
GSHEET_NAME = "FRENDT Leads"

BLACKLIST_FILE = "blacklist_phones.txt"


# ========= GOOGLE DRIVE =========

# ID папок на Google Drive
DRIVE_MAIN_FOLDER_ID = os.getenv(
    "DRIVE_MAIN_FOLDER_ID",
    "1ohaGACNfuQo2QrdMxNK3LyoL5kL08Odk",  # FRENDT Bot Media (основна)
).strip()

DRIVE_SERVICE_FOLDER_ID = os.getenv(
    "DRIVE_SERVICE_FOLDER_ID",
    "1h30HfiZNZQh8XMGyPBG5YTqWhJPuDU_L",  # Service
).strip()

DRIVE_DEFAULT_FOLDER_ID = os.getenv(
    "DRIVE_DEFAULT_FOLDER_ID",
    "1Q_VAJyPPV_LrUUwA56BwH4hLAR85DpXt",  # Default / інші
).strip()

DRIVE_CABLE_FOLDER_ID = os.getenv(
    "DRIVE_CABLE_FOLDER_ID",
    "1RWiwffWpCwUJ56CnxYhwLc9V00NjSXnN",  # Cables
).strip()


def choose_run_mode() -> str:
    """Вибираємо режим запуску бота: polling або webhook."""
    env_mode = (os.getenv("RUN_MODE", "") or "").strip().lower()
    webhook_base = (os.getenv("WEBHOOK_BASE", "") or "").strip()
    print(
        f"[diag] RUN_MODE={env_mode or '∅'} "
        f"WEBHOOK_BASE={'set' if webhook_base else '∅'} "
        f"PORT={os.getenv('PORT', '∅')}"
    )
    if env_mode.startswith("poll"):
        return "polling"
    if env_mode.startswith("web") and webhook_base:
        return "webhook"
    if not webhook_base:
        return "polling"
    return "polling"


# ========= OpenAI CLIENT =========

if FREE_MODE:
    OPENAI_CLIENT = None
else:
    from openai import OpenAI

    OPENAI_CLIENT = OpenAI(api_key=OPENAI_API_KEY)

# ... внизу config.py

def model_display_name(model_id: str) -> str:
    """
    Людська назва моделі для відповіді користувачу.
    Якщо немає в мапі — повертаємо як є.
    """
    mapping = {
        "gpt-4o-mini": "GPT-4o mini",
        "gpt-4o": "GPT-4o",
        "gpt-4.1-mini": "GPT-4.1 mini",
        "gpt-4.1": "GPT-4.1",
        "gpt-5-mini": "GPT-5 mini",
        "gpt-5.1-mini": "GPT-5.1 mini",
        "gpt-5.1": "GPT-5.1",
    }
    return mapping.get(model_id, model_id)
