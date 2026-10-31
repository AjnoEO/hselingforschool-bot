import os
from enum import Enum
from contextlib import contextmanager
import sqlite3
from enums import OlympStatus, QueueStatus, BlockType, Platform

__DEFAULT_DATABASE_DIR = "database"
__DATABASE_FILE = "olymp.db"
_DATABASE_DEFAULT = os.path.join(__DEFAULT_DATABASE_DIR, __DATABASE_FILE)
# Путь к базе можно переопределить переменной окружения (используется в тестах)
DATABASE = os.environ.get("OLYMP_DB", _DATABASE_DEFAULT)
__DATABASE_DIR = os.path.dirname(os.path.abspath(DATABASE))
DB_VERSION = 8
DB_VERSION_FILE = os.path.join(__DATABASE_DIR, "version.txt")
__SCRIPT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), __DEFAULT_DATABASE_DIR)
SCRIPT_FILE = os.path.join(__SCRIPT_DIR, "db.sql")

@contextmanager
def db_connection(db: str = DATABASE):
    conn = sqlite3.connect(db)
    try:
        yield conn
    finally:
        conn.close()

def set_enum(enum_type: type[Enum], table: str, cursor: sqlite3.Cursor):
    for e in list(enum_type):
        id = e.value
        name = e.name
        cursor.execute(f"SELECT 1 FROM {table} WHERE id = {id}")
        fetch = cursor.fetchone()
        if fetch:
            q = f"UPDATE {table} SET id = ?, name = ? WHERE id = {id}"
        else:
            q = f"INSERT INTO {table} (id, name) VALUES (?, ?)"
        cursor.execute(q, (id, name))
    cursor.connection.commit()

def create_update_db():
    os.makedirs(__DATABASE_DIR, exist_ok=True)
    if not os.path.exists(DATABASE):
        with open(SCRIPT_FILE, encoding="utf8") as f:
            script = f.read()
        with db_connection() as con:
            cur = con.cursor()
            cur.executescript(script)
            con.commit()
    else:
        with open(DB_VERSION_FILE) as f:
            current_version = int(f.read())
        if current_version != DB_VERSION:
            scripts = []
            for version in range(current_version + 1, DB_VERSION + 1):
                update_file = os.path.join(__SCRIPT_DIR, f"update_{version}.sql")
                with open(update_file, encoding="utf8") as f:
                    scripts.append(f.read())
            with db_connection() as con:
                cur = con.cursor()
                for script in scripts:
                    cur.executescript(script)
                con.commit()
    with open(DB_VERSION_FILE, "w") as f:
        f.write(str(DB_VERSION))
    with db_connection() as con:
        cur = con.cursor()
        set_enum(OlympStatus, "olymp_status", cursor=cur)
        set_enum(QueueStatus, "queue_status", cursor=cur)
        set_enum(BlockType, "block_types", cursor=cur)
        set_enum(Platform, "platforms", cursor=cur)

if __name__ == "__main__":
    create_update_db()
