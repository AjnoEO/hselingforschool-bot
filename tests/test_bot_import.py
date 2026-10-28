import os
import subprocess
import sys

from tests import DatabaseTestCase, REPO_DIR, TEST_DIR


class BotImportTest(DatabaseTestCase):
    def test_import_does_not_create_database(self):
        database = os.path.join(TEST_DIR, "import-test.db")
        if os.path.exists(database):
            os.remove(database)
        log_path = os.path.join(TEST_DIR, "import-test.log")
        env = os.environ.copy()
        env["OLYMP_DB"] = database
        with open(log_path, "w", encoding="utf8") as log:
            result = subprocess.run(
                [sys.executable, "-c", "import bot"],
                cwd=REPO_DIR, env=env, stdout=log, stderr=subprocess.STDOUT
            )
        with open(log_path, encoding="utf8") as log:
            self.assertEqual(result.returncode, 0, log.read())
        self.assertFalse(os.path.exists(database))
