import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from scripts import _migrate_db

LEGACY_SCHEMA = """
CREATE TABLE jobs (
    id TEXT PRIMARY KEY, entry_date TEXT, modified_date TEXT,
    company TEXT, favicon TEXT, title TEXT, link TEXT, description TEXT,
    keywords TEXT, resume TEXT,
    item_json TEXT NOT NULL CHECK(json_valid(item_json) AND json_type(item_json) = 'object')
);
CREATE TABLE resumes (
    id TEXT PRIMARY KEY, entry_date TEXT, modified_date TEXT,
    technologies TEXT, skills TEXT, responsibilities TEXT, positions TEXT,
    checklist TEXT, education TEXT, languages TEXT, awards TEXT,
    projects TEXT, contact TEXT, job TEXT REFERENCES jobs(id),
    basis_for_resumes TEXT, successful INTEGER,
    item_json TEXT NOT NULL CHECK(json_valid(item_json) AND json_type(item_json) = 'object')
);
CREATE VIEW applications AS
SELECT r.successful AS successful, j.id AS job_id, r.id AS resume_id,
       j.item_json AS job_item, r.item_json AS resume_item
FROM jobs j JOIN resumes r ON j.id = r.job;
"""


class MigrationTests(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "records.sqlite3"
        self.backup = self.path.with_name(self.path.name + ".before-ats-db.sqlite3")
        with closing(sqlite3.connect(self.path)) as conn, conn:
            conn.executescript(LEGACY_SCHEMA)
            conn.execute(
                "INSERT INTO jobs (id, entry_date, modified_date, company, keywords, "
                "item_json) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    "job-1",
                    None,
                    "1999-01-01",
                    "Example",
                    '{ "technologies": ["Python"] }',
                    json.dumps({"id": "job-1", "custom": {"kept": True}}, indent=2),
                ),
            )
            conn.execute(
                "INSERT INTO resumes (id, skills, awards, job, successful, item_json) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    "resume-1",
                    '[ "Python" ]',
                    '[["Award", "Details"]]',
                    "job-1",
                    None,
                    '{ "id": "stale-copy", "skills": ["Python"], "custom": true }',
                ),
            )

    def snapshot(self, path: Path) -> dict[str, list[tuple]]:
        with closing(sqlite3.connect(path)) as conn, conn:
            return {
                table: conn.execute(
                    f"SELECT rowid, * FROM {table} ORDER BY rowid"
                ).fetchall()
                for table in ("jobs", "resumes")
            }

    def definitions(self, path: Path) -> list[tuple]:
        with closing(sqlite3.connect(path)) as conn, conn:
            return conn.execute(
                "SELECT type, name, sql FROM sqlite_schema ORDER BY type, name"
            ).fetchall()

    def test_migration_preserves_rows_json_metadata_and_backup(self) -> None:
        before = self.snapshot(self.path)
        definitions = self.definitions(self.path)
        self.assertEqual(_migrate_db.migrate_database(self.path), self.backup)
        self.assertEqual(self.snapshot(self.path), before)
        self.assertEqual(self.snapshot(self.backup), before)
        self.assertEqual(self.definitions(self.backup), definitions)
        with closing(sqlite3.connect(self.path)) as conn, conn:
            self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])
            self.assertEqual(
                conn.execute("PRAGMA integrity_check").fetchall(), [("ok",)]
            )
            application = conn.execute("SELECT * FROM applications").fetchone()
            self.assertEqual(application[1:3], ("job-1", "resume-1"))

    def test_new_inserts_get_defaults_and_updates_get_timestamps(self) -> None:
        _migrate_db.migrate_database(self.path)
        with closing(sqlite3.connect(self.path)) as conn, conn:
            conn.execute("INSERT INTO jobs (item_json) VALUES ('{}')")
            job = conn.execute(
                "SELECT id, entry_date, modified_date FROM jobs WHERE id != 'job-1'"
            ).fetchone()
            self.assertRegex(job[0], r"^[0-9a-f]{32}$")
            self.assertIsInstance(job[1], str)
            self.assertIsInstance(job[2], str)
            conn.execute("INSERT INTO resumes (item_json) VALUES ('{}')")
            resume = conn.execute(
                "SELECT id, entry_date, successful FROM resumes WHERE id != 'resume-1'"
            ).fetchone()
            self.assertRegex(resume[0], r"^[0-9a-f]{32}$")
            self.assertIsInstance(resume[1], str)
            self.assertEqual(resume[2], 0)
            conn.execute("UPDATE jobs SET title = 'Updated' WHERE id = 'job-1'")
            modified, payload = conn.execute(
                "SELECT modified_date, item_json FROM jobs WHERE id = 'job-1'"
            ).fetchone()
            self.assertNotEqual(modified, "1999-01-01")
            self.assertNotIn("modified_date", json.loads(payload))
        with closing(sqlite3.connect(self.path)) as conn, conn:
            conn.execute("PRAGMA foreign_keys = ON")
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO resumes (job, item_json) VALUES ('missing', '{}')"
                )

    def test_second_run_is_a_no_op(self) -> None:
        _migrate_db.migrate_database(self.path)
        before = self.snapshot(self.path)
        backup = self.backup.read_bytes()
        self.assertIsNone(_migrate_db.migrate_database(self.path))
        self.assertEqual(self.snapshot(self.path), before)
        self.assertEqual(self.backup.read_bytes(), backup)

    def test_existing_backup_is_never_overwritten(self) -> None:
        self.backup.write_bytes(b"existing backup")
        before = self.definitions(self.path)
        with self.assertRaises(FileExistsError):
            _migrate_db.migrate_database(self.path)
        self.assertEqual(self.backup.read_bytes(), b"existing backup")
        self.assertEqual(self.definitions(self.path), before)

    def test_orphans_abort_without_changing_the_database(self) -> None:
        with closing(sqlite3.connect(self.path)) as conn, conn:
            conn.execute("UPDATE resumes SET job = 'missing'")
        before = self.snapshot(self.path)
        definitions = self.definitions(self.path)
        with self.assertRaisesRegex(_migrate_db.MigrationError, "Foreign-key"):
            _migrate_db.migrate_database(self.path)
        self.assertEqual(self.snapshot(self.path), before)
        self.assertEqual(self.definitions(self.path), definitions)
        self.assertFalse(self.backup.exists())

    def test_legacy_database_without_foreign_key_is_supported(self) -> None:
        path = self.path.with_name("without-foreign-key.sqlite3")
        with closing(sqlite3.connect(path)) as conn, conn:
            conn.executescript(
                LEGACY_SCHEMA.replace("job TEXT REFERENCES jobs(id)", "job TEXT")
            )
            conn.execute("INSERT INTO jobs (id, item_json) VALUES ('job-1', '{}')")
            conn.execute(
                "INSERT INTO resumes (id, job, item_json) "
                "VALUES ('resume-1', 'job-1', '{}')"
            )
        before = self.snapshot(path)
        backup = _migrate_db.migrate_database(path)
        self.assertIsNotNone(backup)
        self.assertEqual(self.snapshot(path), before)
        with closing(sqlite3.connect(path)) as conn:
            self.assertEqual(
                conn.execute("PRAGMA foreign_key_list(resumes)").fetchall(),
                [(0, 0, "jobs", "job", "id", "NO ACTION", "NO ACTION", "NONE")],
            )

    def test_missing_foreign_key_orphans_abort_before_backup(self) -> None:
        path = self.path.with_name("orphan-without-foreign-key.sqlite3")
        with closing(sqlite3.connect(path)) as conn, conn:
            conn.executescript(
                LEGACY_SCHEMA.replace("job TEXT REFERENCES jobs(id)", "job TEXT")
            )
            conn.execute(
                "INSERT INTO resumes (id, job, item_json) "
                "VALUES ('resume-1', 'missing', '{}')"
            )
        before = self.snapshot(path)
        definitions = self.definitions(path)
        with self.assertRaisesRegex(_migrate_db.MigrationError, "Foreign-key"):
            _migrate_db.migrate_database(path)
        self.assertEqual(self.snapshot(path), before)
        self.assertEqual(self.definitions(path), definitions)
        self.assertFalse(path.with_name(path.name + ".before-ats-db.sqlite3").exists())

    def test_missing_foreign_key_is_added_even_with_current_defaults(self) -> None:
        path = self.path.with_name("current-defaults-without-foreign-key.sqlite3")
        schema = _migrate_db.SCHEMA.read_text(encoding="utf-8")
        with closing(sqlite3.connect(path)) as conn, conn:
            conn.executescript(
                schema.replace("job TEXT REFERENCES jobs(id)", "job TEXT")
            )
        self.assertIsNotNone(_migrate_db.migrate_database(path))
        self.assertIsNone(_migrate_db.migrate_database(path))

    def test_different_existing_foreign_key_is_still_rejected(self) -> None:
        path = self.path.with_name("different-foreign-key.sqlite3")
        with closing(sqlite3.connect(path)) as conn, conn:
            conn.executescript(
                LEGACY_SCHEMA.replace(
                    "job TEXT REFERENCES jobs(id)",
                    "job TEXT REFERENCES jobs(id) ON DELETE CASCADE",
                )
            )
        definitions = self.definitions(path)
        with self.assertRaisesRegex(
            _migrate_db.MigrationError, "foreign-key definition"
        ):
            _migrate_db.migrate_database(path)
        self.assertEqual(self.definitions(path), definitions)

    def test_failure_during_rebuild_rolls_back_and_keeps_backup(self) -> None:
        before = self.snapshot(self.path)
        definitions = self.definitions(self.path)
        rebuild = _migrate_db._rebuild

        def fail_rebuild(conn: sqlite3.Connection, statements: list[str]) -> None:
            rebuild(conn, statements)
            if conn.execute("PRAGMA database_list").fetchone()[2]:
                raise sqlite3.OperationalError("Simulated migration failure")

        with patch.object(_migrate_db, "_rebuild", side_effect=fail_rebuild):
            with self.assertRaisesRegex(sqlite3.OperationalError, "Simulated"):
                _migrate_db.migrate_database(self.path)
        self.assertEqual(self.snapshot(self.path), before)
        self.assertEqual(self.definitions(self.path), definitions)
        self.assertEqual(self.snapshot(self.backup), before)

    def test_custom_indexes_triggers_and_views_are_preserved(self) -> None:
        with closing(sqlite3.connect(self.path)) as conn, conn:
            conn.executescript("""
                CREATE UNIQUE INDEX custom_company ON jobs(company);
                CREATE TABLE audit (resume_id TEXT);
                CREATE TRIGGER custom_insert AFTER INSERT ON resumes
                BEGIN INSERT INTO audit VALUES (NEW.id); END;
                CREATE VIEW custom_jobs AS SELECT id FROM jobs;
            """)
        _migrate_db.migrate_database(self.path)
        with closing(sqlite3.connect(self.path)) as conn, conn:
            self.assertEqual(conn.execute("SELECT * FROM audit").fetchall(), [])
            self.assertEqual(
                conn.execute("SELECT * FROM custom_jobs").fetchall(), [("job-1",)]
            )
            conn.execute("INSERT INTO resumes (id, item_json) VALUES ('new', '{}')")
            self.assertEqual(conn.execute("SELECT * FROM audit").fetchall(), [("new",)])
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO jobs (company, item_json) VALUES ('Example', '{}')"
                )

    def test_missing_database_is_not_created(self) -> None:
        missing = self.path.with_name("missing.sqlite3")
        with self.assertRaises(FileNotFoundError):
            _migrate_db.migrate_database(missing)
        self.assertFalse(missing.exists())

    def test_unexpected_columns_abort_without_data_loss(self) -> None:
        with closing(sqlite3.connect(self.path)) as conn, conn:
            conn.execute("ALTER TABLE jobs ADD COLUMN custom TEXT")
        definitions = self.definitions(self.path)
        with self.assertRaisesRegex(_migrate_db.MigrationError, "Unsupported jobs"):
            _migrate_db.migrate_database(self.path)
        self.assertEqual(self.definitions(self.path), definitions)
        self.assertFalse(self.backup.exists())

    def test_null_ids_and_metadata_are_preserved(self) -> None:
        with closing(sqlite3.connect(self.path)) as conn, conn:
            conn.execute("INSERT INTO jobs (id, item_json) VALUES (NULL, '{}')")
            conn.execute("INSERT INTO jobs (id, item_json) VALUES (NULL, '{}')")
        before = self.snapshot(self.path)
        _migrate_db.migrate_database(self.path)
        self.assertEqual(self.snapshot(self.path), before)

    def test_wal_data_is_included_in_backup(self) -> None:
        with closing(sqlite3.connect(self.path)) as writer, writer:
            writer.execute("PRAGMA journal_mode = WAL")
            writer.execute("INSERT INTO jobs (id, item_json) VALUES ('wal-job', '{}')")
            writer.commit()
            _migrate_db.migrate_database(self.path)
            self.assertEqual(self.snapshot(self.backup), self.snapshot(self.path))

    def test_cli_reports_errors_and_success(self) -> None:
        command = [sys.executable, str(Path(_migrate_db.__file__)), str(self.path)]
        result = subprocess.run(command, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(str(self.backup), result.stdout)
        result = subprocess.run(command, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("already", result.stdout)
        result = subprocess.run(
            [
                sys.executable,
                str(Path(_migrate_db.__file__)),
                str(self.path.with_name("missing.sqlite3")),
            ],
            text=True,
            capture_output=True,
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("Migration failed", result.stderr)


if __name__ == "__main__":
    unittest.main()
