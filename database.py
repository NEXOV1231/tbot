import os
import sqlite3
from pathlib import Path

# Путь к базе можно переопределить переменной окружения TBANK_DB
# (например, в read-only окружениях: TBANK_DB=/tmp/tbank.db).
DB_PATH = Path(__file__).with_name("tbank_demo.db")

def connect():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con

def init_db():
    with connect() as con:
        con.executescript("""
        CREATE TABLE IF NOT EXISTS users(
            user_id TEXT PRIMARY KEY,
            username TEXT NOT NULL DEFAULT '',
            stars INTEGER NOT NULL DEFAULT 0,
            gram INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS operations(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            kind TEXT NOT NULL,
            currency TEXT NOT NULL,
            amount INTEGER NOT NULL,
            status TEXT NOT NULL,
            details TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        -- Обработанные платежи (charge_id уникален: защита от двойного зачисления).
        CREATE TABLE IF NOT EXISTS payments(
            charge_id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            currency TEXT NOT NULL,
            amount INTEGER NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        -- Подарочные чеки: одноразовые коды на сумму Stars/Gram.
        CREATE TABLE IF NOT EXISTS cheques(
            code TEXT PRIMARY KEY,
            creator_id TEXT NOT NULL,
            currency TEXT NOT NULL,
            amount INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'active',
            redeemed_by TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        """)
    # Миграция: баллы кэшбэка появились позже — добавляем колонку, если её нет.
    with connect() as con:
        cols = [row[1] for row in con.execute("PRAGMA table_info(users)").fetchall()]
        if "points" not in cols:
            con.execute("ALTER TABLE users ADD COLUMN points INTEGER NOT NULL DEFAULT 0")
