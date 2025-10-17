import os
from enum import Enum
import sqlite3
from enums import OlympStatus, QueueStatus, BlockType
from telebot.states import State
from telebot.storage.base_storage import StateStorageBase

__DATABASE_DIR = "database"
__DATABASE_FILE = "olymp.db"
DATABASE = os.path.join(__DATABASE_DIR, __DATABASE_FILE)
DB_VERSION = 5
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
    if __DATABASE_FILE not in os.listdir(__DATABASE_DIR):
        with open(SCRIPT_FILE, encoding="utf8") as f:
            script = f.read()
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
    with open(DB_VERSION_FILE, "w") as f:
        f.write(str(DB_VERSION))
    with sqlite3.connect(DATABASE) as con:
        cur = con.cursor()
        set_enum(OlympStatus, "olymp_status", cursor=cur)
        set_enum(QueueStatus, "queue_status", cursor=cur)
        set_enum(BlockType, "block_types", cursor=cur)

class StateDBStorage(StateStorageBase):
    def __init__(
        self,
        database_path: str = DATABASE,
        table_name: str = 'telebot_states'
    ):
        self.database = database_path
        self.table_name = table_name
        q = (f"""
        CREATE TABLE IF NOT EXISTS {self.table_name} (
            chat_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            business_connection_id TEXT,
            message_thread_id INTEGER,
            bot_id INTEGER,
            state TEXT,
            PRIMARY KEY(chat_id, user_id, bot_id)
        )
        """)
        with sqlite3.connect(self.database) as conn:
            cur = conn.cursor()
            cur.execute(q)
            conn.commit()


    @staticmethod
    def __param_columns(param_values: None | list = None):
        param_names = ['chat_id', 'user_id', 'business_connection_id', 'message_thread_id', 'bot_id']
        if param_values:
            param_names = [name + (' = ?' if value else ' IS NULL') for name, value in zip(param_names, param_values)]
            return ' AND '.join(param_names)
        return ', '.join(param_names)


    def set_state(
        self,
        chat_id: int,
        user_id: int,
        state: str,
        business_connection_id: str | None = None,
        message_thread_id: int | None = None,
        bot_id: int | None = None,
    ) -> bool:
        if hasattr(state, "name"):
            state = state.name
        params = [chat_id, user_id, business_connection_id, message_thread_id, bot_id]
        param_columns = self.__param_columns()

        with sqlite3.connect(self.database) as conn:
            cur = conn.cursor()
            q = (f"INSERT INTO {self.table_name} ({param_columns}, state) "
                 f"VALUES ({', '.join('?'*(len(params)+1))}) "
                 f"ON CONFLICT DO UPDATE SET state = excluded.state")
            cur.execute(
                q,
                tuple(params + [state])
            )
            conn.commit()
        return True
    

    def get_state(
        self,
        chat_id: int,
        user_id: int,
        business_connection_id: str | None = None,
        message_thread_id: int | None = None,
        bot_id: int | None = None,
    ) -> str | None:
        params = [chat_id, user_id, business_connection_id, message_thread_id, bot_id]
        param_columns = self.__param_columns(params)
        while None in params:
            params.remove(None)

        with sqlite3.connect(self.database) as conn:
            cur = conn.cursor()
            cur.execute(
                f"SELECT state FROM {self.table_name} WHERE {param_columns}",
                tuple(params)
            )
            fetch = cur.fetchone()
        return fetch[0] if fetch else None


    def delete_state(
        self,
        chat_id: int,
        user_id: int,
        business_connection_id: str | None = None,
        message_thread_id: int | None = None,
        bot_id: int | None = None,
    ) -> bool:
        params = [chat_id, user_id, business_connection_id, message_thread_id, bot_id]
        param_columns = self.__param_columns(params)
        while None in params:
            params.remove(None)

        with sqlite3.connect(self.database) as conn:
            cur = conn.cursor()
            cur.execute(
                f"DELETE FROM {self.table_name} WHERE {param_columns}",
                tuple(params)
            )
            conn.commit()
        return True
