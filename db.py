import os
from enum import Enum
import sqlite3
from enums import OlympStatus, QueueStatus, BlockType

__DATABASE_DIR = "database"
DATABASE = os.path.join(__DATABASE_DIR, "olymp.db")
DB_VERSION = 2
DB_VERSION_FILE = os.path.join(__DATABASE_DIR, "version.txt")
SCRIPT_FILE = os.path.join(__DATABASE_DIR, "db.sql")

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
    if DATABASE not in os.listdir():
        with open(SCRIPT_FILE, encoding="utf8") as f:
            script = f.read()
        with open(DB_VERSION_FILE, "w") as f:
            f.write(DB_VERSION)
        with sqlite3.connect(DATABASE) as con:
            cur = con.cursor()
            cur.executescript(script)
            con.commit()
    else:
        with open(DB_VERSION_FILE) as f:
            current_version = int(f.read())
        if current_version != DB_VERSION:
            scripts = []
            for version in range(current_version + 1, DB_VERSION + 1):
                update_file = os.path.join(__DATABASE_DIR, f"update_{version}.sql")
                with open(update_file, encoding="utf8") as f:
                    scripts.append(f.read())
            with sqlite3.connect(DATABASE) as con:
                cur = con.cursor()
                for script in scripts:
                    cur.executescript(script)
                con.commit()
    set_enum(OlympStatus, "olymp_status", cursor=cur)
    set_enum(QueueStatus, "queue_status", cursor=cur)
    set_enum(BlockType, "block_types", cursor=cur)
