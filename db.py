from enum import Enum
import sqlite3
from enums import OlympStatus, QueueStatus, BlockType

DATABASE = "olymp.db"

def set_enum(enum_type: Enum.__class__, table: str, cursor: sqlite3.Cursor):
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

def create_tables(script_file: str):
    with open(script_file, encoding="utf8") as f:
        script = f.read()
    with sqlite3.connect(DATABASE) as con:
        cur = con.cursor()
        cur.executescript(script)
        con.commit()
        set_enum(OlympStatus, "olymp_status", cursor=cur)
        set_enum(QueueStatus, "queue_status", cursor=cur)
        set_enum(BlockType, "block_types", cursor=cur)
