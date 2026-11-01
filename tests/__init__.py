# Запуск: python -m unittest discover -s tests -t .

import atexit
import os
import shutil
import unittest
from configparser import ConfigParser

# Тесты работают с отдельной базой и отдельным конфигом, чтобы не трогать рабочие файлы.
# Временную папку создаём внутри репозитория: системная папка temp может быть недоступна.
REPO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP_DIR = os.path.join(REPO_DIR, ".tmp")
os.makedirs(TMP_DIR, exist_ok=True)
shutil.rmtree(TMP_DIR, ignore_errors=True)
TEST_DIR = os.path.join(TMP_DIR, f"olymp-tests-{os.getpid()}")
os.makedirs(TEST_DIR)
atexit.register(shutil.rmtree, TEST_DIR, ignore_errors=True)

os.environ["OLYMP_DB"] = os.path.join(TEST_DIR, "olymp.db")
os.environ["OLYMP_CONFIG"] = os.path.join(TEST_DIR, "config.ini")

__config = ConfigParser()
__config["data"] = {
    "token": "000000:TEST_TOKEN",
    "owner_id": "1",
    "owner_handle": "@owner",
}
__config["bot"] = {
    "promote_commands": "false",
    "no_examiner_complaints": "false",
    "member_page_size": "10",
    "ask_people_handles": "@organizer",
}
with open(os.environ["OLYMP_CONFIG"], "w", encoding="utf8") as f:
    __config.write(f)

# Импорт после настройки переменных окружения: db читает OLYMP_DB при импорте
import db


class DatabaseTestCase(unittest.TestCase):
    """Тест с чистой базой данных перед каждым тестом"""

    def setUp(self):
        test_database = os.environ.get("OLYMP_DB")
        if not test_database or os.path.abspath(db._DATABASE_DEFAULT) == os.path.abspath(test_database):
            raise RuntimeError("Тестовая база не должна совпадать с рабочей базой")
        if os.path.abspath(test_database) != os.path.abspath(db.DATABASE):
            raise RuntimeError("db.DATABASE должно определить тестовую базу из переменной среды")
        if not os.path.exists(db.DATABASE):
            db.create_update_db()
        with db.db_connection() as conn:
            cur = conn.cursor()
            for table in (
                "user_tags", "examiner_problems", "queue", "participants",
                "examiners", "problem_blocks", "problems", "tags", "olymps", "users",
            ):
                cur.execute(f"DELETE FROM {table}")
            conn.commit()
