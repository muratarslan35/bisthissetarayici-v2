import sqlite3
from pathlib import Path

# ======================================================
# DATABASE PATH
# ======================================================

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "system.db"

# ======================================================
# CONNECTION
# ======================================================

def get_connection():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=10000")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn

# ======================================================
# INIT DATABASE
# ======================================================

def init_db():
    conn = get_connection()
    cur = conn.cursor()

    # Separate web + scanner processes share this DB. WAL allows readers
    # to continue while the worker persists scanner/trade updates.
    cur.execute("PRAGMA journal_mode=WAL")
    cur.execute("PRAGMA synchronous=NORMAL")

    # ==================================================
    # USERS TABLE
    # ==================================================

    cur.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,

        username TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,

        telegram_chat_id TEXT UNIQUE,

        subscription_end TEXT,
        is_active INTEGER DEFAULT 1,

        status TEXT DEFAULT 'pending',
        channel_status TEXT DEFAULT 'pending',

        invite_sent INTEGER DEFAULT 0,
        invite_link TEXT,

        active_session_id TEXT,
        last_login_at TEXT,

        registered_ip TEXT,

        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )
    """)

    # ==================================================
    # INVITE CODES TABLE
    # ==================================================

    cur.execute("""
    CREATE TABLE IF NOT EXISTS invite_codes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,

        code TEXT UNIQUE NOT NULL,

        is_used INTEGER DEFAULT 0,
        used_by TEXT,

        expires_at TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )
    """)

    # ==================================================
    # LOGIN LOGS
    # ==================================================

    cur.execute("""
    CREATE TABLE IF NOT EXISTS login_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT,
        ip TEXT,
        success INTEGER,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )
    """)

    # ==================================================
    # RATE LIMIT TABLE
    # ==================================================

    cur.execute("""
    CREATE TABLE IF NOT EXISTS rate_limits (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        identifier TEXT,
        request_count INTEGER,
        last_request TEXT
    )
    """)

    # ==================================================
    # SIGNAL LOGS
    # ==================================================

    cur.execute("""
    CREATE TABLE IF NOT EXISTS signal_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        symbol TEXT,
        algorithm TEXT,
        signal_type TEXT,
        price REAL,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )
    """)

    conn.commit()

    # ==================================================
    # SAFE COLUMN CHECK (Backward Compatibility)
    # ==================================================

    ensure_column_exists(cur, "users", "telegram_chat_id", "TEXT UNIQUE")
    ensure_column_exists(cur, "users", "subscription_end", "TEXT")
    ensure_column_exists(cur, "users", "is_active", "INTEGER DEFAULT 1")
    ensure_column_exists(cur, "users", "status", "TEXT DEFAULT 'pending'")
    ensure_column_exists(cur, "users", "channel_status", "TEXT DEFAULT 'pending'")
    ensure_column_exists(cur, "users", "invite_sent", "INTEGER DEFAULT 0")
    ensure_column_exists(cur, "users", "invite_link", "TEXT")
    ensure_column_exists(cur, "users", "active_session_id", "TEXT")
    ensure_column_exists(cur, "users", "last_login_at", "TEXT")
    ensure_column_exists(cur, "users", "registered_ip", "TEXT")
    ensure_column_exists(cur, "users", "is_admin", "INTEGER DEFAULT 0")
    ensure_column_exists(cur, "users", "expiry_warning_sent", "INTEGER DEFAULT 0")
    ensure_column_exists(cur, "invite_codes", "is_used", "INTEGER DEFAULT 0")
    ensure_column_exists(cur, "invite_codes", "used_by", "TEXT")
    ensure_column_exists(cur, "invite_codes", "expires_at", "TEXT")

    conn.commit()
    conn.close()

# ======================================================
# SAFE ALTER FUNCTION
# ======================================================

def ensure_column_exists(cursor, table, column, definition):
    cursor.execute(f"PRAGMA table_info({table})")
    columns = [row["name"] for row in cursor.fetchall()]

    if column not in columns:
        cursor.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
