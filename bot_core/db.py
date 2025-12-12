import psycopg2
from psycopg2.extras import DictCursor

from .config import DATABASE_URL
from .logging_setup import logger
from .utils import normalize_phone, is_blacklisted

# внутрішній прапорець: чи пробували вже підключитись
_DB_ENABLED = bool(DATABASE_URL)


class _DummyCursor:
    def execute(self, *a, **k): pass
    def fetchone(self): return None
    def close(self): pass


class _DummyConn:
    def cursor(self): return _DummyCursor()
    def commit(self): pass
    def close(self): pass


def db_connect():
    """
    Підключення до PostgreSQL.
    Якщо не вдається підключитися — логуємо помилку, вимикаємо _DB_ENABLED
    і повертаємо _DummyConn(), щоб бот працював без БД, а не падав.
    """
    global _DB_ENABLED

    if not _DB_ENABLED or not DATABASE_URL:
        return _DummyConn()

    try:
        return psycopg2.connect(DATABASE_URL, cursor_factory=DictCursor)
    except Exception as e:
        logger.warning("DB connect failed (%s). Працюю без PostgreSQL (DummyConn).", e)
        _DB_ENABLED = False
        return _DummyConn()


def db_init():
    if not _DB_ENABLED:
        # у dev-режимі БД не використовуємо
        return
    con = db_connect()
    cur = con.cursor()

    # leads (історичний список контактів)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS leads (
        id SERIAL PRIMARY KEY,
        first_name TEXT,
        last_name  TEXT,
        username   TEXT,
        phone      TEXT,
        created_at TIMESTAMP DEFAULT NOW()
    );
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_leads_phone ON leads(phone);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_leads_username ON leads(username);")

    # постійна «пам'ять» номерів по tg_user_id
    cur.execute("""
    CREATE TABLE IF NOT EXISTS user_contacts (
        tg_user_id TEXT PRIMARY KEY,
        phone      TEXT,
        full_name  TEXT,
        created_at TIMESTAMP DEFAULT NOW(),
        updated_at TIMESTAMP DEFAULT NOW()
    );
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_uc_phone ON user_contacts(phone);")

    # повідомлення (перші звернення та інше)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS lead_messages (
        id SERIAL PRIMARY KEY,
        tg_user_id TEXT,
        phone      TEXT,
        full_name  TEXT,
        text       TEXT,
        created_at TIMESTAMP DEFAULT NOW()
    );
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_messages_tg ON lead_messages(tg_user_id);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_messages_phone ON lead_messages(phone);")

    con.commit()
    con.close()


def db_lead_exists_by_phone(phone: str) -> bool:
    con = db_connect(); cur = con.cursor()
    cur.execute("SELECT 1 FROM leads WHERE phone = %s LIMIT 1", (phone,))
    row = cur.fetchone()
    con.close()
    return row is not None


def db_save_lead(first_name: str, last_name: str, username: str, phone: str) -> bool:
    norm = normalize_phone(phone)
    if is_blacklisted(norm):
        return False
    if db_lead_exists_by_phone(norm):
        return False
    con = db_connect(); cur = con.cursor()
    cur.execute("""
        INSERT INTO leads (first_name, last_name, username, phone, created_at)
        VALUES (%s, %s, %s, %s, NOW())
    """, (first_name, last_name, username, norm))
    con.commit(); con.close()
    return True


def db_save_first_message(phone: str, full_name: str, text: str, tg_user_id: str | None = None) -> None:
    norm = normalize_phone(phone)
    if is_blacklisted(norm):
        return
    con = db_connect(); cur = con.cursor()
    cur.execute("""
        INSERT INTO lead_messages (tg_user_id, phone, full_name, text, created_at)
        VALUES (%s, %s, %s, %s, NOW())
    """, (str(tg_user_id) if tg_user_id else None, norm, full_name, text))
    con.commit(); con.close()


# --- «пам'ять» контактів за tg_user_id ---

def db_get_known_phone_by_tg(tg_user_id: str) -> str | None:
    if not tg_user_id:
        return None
    con = db_connect(); cur = con.cursor()
    cur.execute("SELECT phone FROM user_contacts WHERE tg_user_id = %s LIMIT 1", (tg_user_id,))
    row = cur.fetchone()
    con.close()
    if not row:
        return None
    try:
        phone = row[0]
    except Exception:
        try:
            phone = row.get("phone")
        except Exception:
            phone = None
    if phone:
        return normalize_phone(phone)
    return None


def db_set_known_phone(tg_user_id: str, phone: str, full_name: str = "") -> None:
    if not tg_user_id or not phone:
        return
    con = db_connect(); cur = con.cursor()
    cur.execute("""
        INSERT INTO user_contacts (tg_user_id, phone, full_name)
        VALUES (%s, %s, %s)
        ON CONFLICT (tg_user_id)
        DO UPDATE SET phone = EXCLUDED.phone,
                      full_name = EXCLUDED.full_name,
                      updated_at = NOW()
    """, (str(tg_user_id), normalize_phone(phone), full_name or ""))
    con.commit(); con.close()
