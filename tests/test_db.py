import os
import sqlite3

import db
from enums import BlockType, OlympStatus, QueueStatus
from tests import DatabaseTestCase


class CreateUpdateDbTest(DatabaseTestCase):
    def test_schema_created(self):
        self.assertTrue(os.path.exists(db.DATABASE))
        with sqlite3.connect(db.DATABASE) as conn:
            cur = conn.cursor()
            cur.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
            tables = {fetch[0] for fetch in cur.fetchall()}
        for table in (
            "olymp_status", "queue_status", "block_types", "tags", "olymps",
            "users", "user_tags", "problems", "problem_blocks", "participants",
            "examiners", "examiner_problems", "queue",
        ):
            self.assertIn(table, tables)

    def test_version_file_written(self):
        with open(db.DB_VERSION_FILE, encoding="utf8") as f:
            self.assertEqual(int(f.read()), db.DB_VERSION)

    def test_enums_filled(self):
        with sqlite3.connect(db.DATABASE) as conn:
            cur = conn.cursor()
            for table, enum_type in (
                ("olymp_status", OlympStatus),
                ("queue_status", QueueStatus),
                ("block_types", BlockType),
            ):
                cur.execute(f"SELECT COUNT(*) FROM {table}")
                self.assertEqual(cur.fetchone()[0], len(enum_type))

    def test_create_update_db_is_idempotent(self):
        db.create_update_db()
        db.create_update_db()
        with open(db.DB_VERSION_FILE, encoding="utf8") as f:
            self.assertEqual(int(f.read()), db.DB_VERSION)

    def test_database_path_comes_from_environment(self):
        self.assertEqual(db.DATABASE, os.environ["OLYMP_DB"])


class StateDBStorageTest(DatabaseTestCase):
    def test_table_created_on_first_use(self):
        with sqlite3.connect(db.DATABASE) as conn:
            conn.execute("DROP TABLE IF EXISTS telebot_states")
            conn.commit()

        storage = db.StateDBStorage()
        with sqlite3.connect(db.DATABASE) as conn:
            cur = conn.cursor()
            cur.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'telebot_states'")
            self.assertIsNone(cur.fetchone())

        storage.set_state(chat_id=1, user_id=2, state="test")
        self.assertEqual(storage.get_state(chat_id=1, user_id=2), "test")
        storage.delete_state(chat_id=1, user_id=2)
        self.assertIsNone(storage.get_state(chat_id=1, user_id=2))
