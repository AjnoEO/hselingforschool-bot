import os

import db
from enums import BlockType, OlympStatus, Platform, QueueStatus
from tests import DatabaseTestCase


class CreateUpdateDbTest(DatabaseTestCase):
    def test_schema_created(self):
        self.assertTrue(os.path.exists(db.DATABASE))
        with db.db_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
            tables = {fetch[0] for fetch in cur.fetchall()}
        for table in (
            "olymp_status", "queue_status", "block_types", "platforms", "tags",
            "olymps", "users", "conversation_states", "user_tags", "problems",
            "problem_blocks", "participants", "examiners", "examiner_problems", "queue",
        ):
            self.assertIn(table, tables)

    def test_version_file_written(self):
        with open(db.DB_VERSION_FILE, encoding="utf8") as f:
            self.assertEqual(int(f.read()), db.DB_VERSION)

    def test_enums_filled(self):
        with db.db_connection() as conn:
            cur = conn.cursor()
            for table, enum_type in (
                ("olymp_status", OlympStatus),
                ("queue_status", QueueStatus),
                ("block_types", BlockType),
                ("platforms", Platform),
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

    def test_version_file_next_to_database(self):
        self.assertEqual(
            os.path.dirname(os.path.abspath(db.DB_VERSION_FILE)),
            os.path.dirname(os.path.abspath(db.DATABASE)),
        )

    def test_users_identity_columns(self):
        with db.db_connection() as conn:
            cur = conn.cursor()
            cur.execute("PRAGMA table_info(users)")
            columns = {fetch[1] for fetch in cur.fetchall()}
        self.assertTrue({"platform", "external_id", "handle"} <= columns)
        self.assertNotIn("tg_id", columns)
        self.assertNotIn("tg_handle", columns)
