import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from ats_models import application_from_row, resume_from_row
from scripts import _migrate_db, _migrate_remove_item_json


class RemoveItemJsonTests(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "records.sqlite3"
        self.backup = self.path.with_name(
            self.path.name + ".before-remove-item-json.sqlite3"
        )
        with closing(sqlite3.connect(self.path)) as conn, conn:
            conn.executescript(_migrate_db.SCHEMA.read_text(encoding="utf-8"))
            conn.execute(
                "INSERT INTO jobs (id, entry_date, keywords, item_json) "
                "VALUES (?, ?, ?, ?)",
                (
                    "job-1",
                    None,
                    '{ "technologies": ["Python"] }',
                    '{"id": "stale", "custom": {"discard": true}}',
                ),
            )
            conn.execute(
                "INSERT INTO resumes (id, skills, awards, job, basis_for_resumes, "
                "successful, item_json) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    "resume-1",
                    '[ "Python" ]',
                    '[["Award", "Details"]]',
                    "job-1",
                    '["original"]',
                    None,
                    '{"custom": true}',
                ),
            )

    def snapshot(self, path: Path, *, include_item_json: bool = False) -> dict:
        with closing(sqlite3.connect(path)) as conn:
            conn.row_factory = sqlite3.Row
            return {
                table: [
                    {
                        key: value
                        for key, value in dict(row).items()
                        if include_item_json or key != "item_json"
                    }
                    for row in conn.execute(
                        f"SELECT rowid, * FROM {table} ORDER BY rowid"
                    )
                ]
                for table in ("jobs", "resumes")
            }

    def test_second_migration_preserves_columns_and_backs_up_removed_data(self) -> None:
        before = self.snapshot(self.path)
        complete_before = self.snapshot(self.path, include_item_json=True)
        self.assertEqual(
            _migrate_remove_item_json.migrate_database(self.path), self.backup
        )
        self.assertEqual(self.snapshot(self.path), before)
        self.assertEqual(
            self.snapshot(self.backup, include_item_json=True), complete_before
        )
        with closing(sqlite3.connect(self.path)) as conn:
            for table in ("jobs", "resumes"):
                columns = [
                    row[1] for row in conn.execute(f"PRAGMA table_info({table})")
                ]
                self.assertNotIn("item_json", columns)
            self.assertEqual(
                conn.execute("PRAGMA integrity_check").fetchall(), [("ok",)]
            )
            self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_readers_decode_authoritative_columns_and_legacy_shapes(self) -> None:
        _migrate_remove_item_json.migrate_database(self.path)
        with closing(sqlite3.connect(self.path)) as conn:
            conn.row_factory = sqlite3.Row
            record = resume_from_row(
                dict(conn.execute("SELECT * FROM resumes").fetchone())
            )
        self.assertEqual(record.id, "resume-1")
        self.assertEqual(record.skills, [["Python"]])
        self.assertEqual(record.awards[0].title, "Award")
        self.assertEqual(record.basis_for_resumes, ["original"])
        self.assertIsNone(record.successful)
        self.assertNotIn("custom", record.model_extra or {})

    def test_application_view_computes_nested_json_from_columns(self) -> None:
        _migrate_remove_item_json.migrate_database(self.path)
        with closing(sqlite3.connect(self.path)) as conn:
            conn.row_factory = sqlite3.Row
            row = dict(conn.execute("SELECT * FROM applications").fetchone())
        job = json.loads(row["job_item"])
        resume = json.loads(row["resume_item"])
        self.assertEqual(job["id"], "job-1")
        self.assertIsInstance(job["keywords"], dict)
        self.assertEqual(resume["skills"], ["Python"])
        self.assertNotIn("custom", job)
        application = application_from_row(row)
        self.assertEqual(application.resume_item.skills, [["Python"]])

    def test_json_checks_reject_malformed_and_wrong_root_types(self) -> None:
        _migrate_remove_item_json.migrate_database(self.path)
        with closing(sqlite3.connect(self.path)) as conn:
            for table, columns, wrong_root in (
                ("jobs", ("keywords",), "[]"),
                ("resumes", ("technologies", "education", "contact"), "[]"),
                (
                    "resumes",
                    (
                        "skills",
                        "positions",
                        "checklist",
                        "languages",
                        "awards",
                        "projects",
                    ),
                    "{}",
                ),
            ):
                for column in columns:
                    for value in ("not JSON", wrong_root):
                        with self.subTest(column=column, value=value):
                            with self.assertRaises(sqlite3.DatabaseError):
                                conn.execute(
                                    f"INSERT INTO {table} ({column}) VALUES (?)",
                                    (value,),
                                )
                            conn.rollback()
            with self.assertRaises(sqlite3.DatabaseError):
                conn.execute(
                    "INSERT INTO resumes (basis_for_resumes) VALUES ('not JSON')"
                )

    def test_invalid_legacy_json_aborts_with_original_schema_and_backup(self) -> None:
        with closing(sqlite3.connect(self.path)) as conn, conn:
            conn.execute("UPDATE resumes SET skills = 'not JSON'")
        before = self.snapshot(self.path, include_item_json=True)
        with self.assertRaises(sqlite3.DatabaseError):
            _migrate_remove_item_json.migrate_database(self.path)
        self.assertEqual(self.snapshot(self.path, include_item_json=True), before)
        self.assertEqual(self.snapshot(self.backup, include_item_json=True), before)

    def test_dependent_custom_triggers_abort_before_removal(self) -> None:
        with closing(sqlite3.connect(self.path)) as conn, conn:
            conn.executescript("""
                CREATE TABLE audit (payload TEXT);
                CREATE TRIGGER legacy_audit AFTER UPDATE ON jobs
                BEGIN INSERT INTO audit VALUES (NEW.item_json); END;
            """)
        before = self.snapshot(self.path, include_item_json=True)
        with self.assertRaisesRegex(_migrate_db.MigrationError, "legacy_audit"):
            _migrate_remove_item_json.migrate_database(self.path)
        self.assertEqual(self.snapshot(self.path, include_item_json=True), before)
        self.assertFalse(self.backup.exists())

    def test_second_run_is_a_no_op(self) -> None:
        _migrate_remove_item_json.migrate_database(self.path)
        backup = self.backup.read_bytes()
        self.assertIsNone(_migrate_remove_item_json.migrate_database(self.path))
        self.assertEqual(self.backup.read_bytes(), backup)

    def test_existing_columns_without_json_checks_are_upgraded(self) -> None:
        path = self.path.with_name("missing-json-checks.sqlite3")
        schema = _migrate_remove_item_json.SCHEMA.read_text(encoding="utf-8")
        unchecked = "\n".join(
            (
                line.split(" CHECK(", 1)[0]
                + ("," if line.rstrip().endswith(",") else "")
                if " CHECK(" in line
                else line
            )
            for line in schema.splitlines()
        )
        with closing(sqlite3.connect(path)) as conn:
            conn.executescript(unchecked)
        self.assertIsNotNone(_migrate_remove_item_json.migrate_database(path))
        with closing(sqlite3.connect(path)) as conn:
            with self.assertRaises(sqlite3.DatabaseError):
                conn.execute("INSERT INTO resumes (skills) VALUES ('not JSON')")

    def test_existing_backup_is_not_overwritten(self) -> None:
        self.backup.write_bytes(b"keep")
        before = self.snapshot(self.path, include_item_json=True)
        with self.assertRaises(FileExistsError):
            _migrate_remove_item_json.migrate_database(self.path)
        self.assertEqual(self.backup.read_bytes(), b"keep")
        self.assertEqual(self.snapshot(self.path, include_item_json=True), before)

    def test_dependent_custom_views_abort_instead_of_becoming_broken(self) -> None:
        with closing(sqlite3.connect(self.path)) as conn, conn:
            conn.execute("CREATE VIEW legacy_payloads AS SELECT item_json FROM jobs")
        before = self.snapshot(self.path, include_item_json=True)
        with self.assertRaisesRegex(_migrate_db.MigrationError, "legacy_payloads"):
            _migrate_remove_item_json.migrate_database(self.path)
        self.assertEqual(self.snapshot(self.path, include_item_json=True), before)

    def test_first_migration_remains_reproducible(self) -> None:
        self.assertIsNone(_migrate_db.migrate_database(self.path))
        _migrate_remove_item_json.migrate_database(self.path)
        with self.assertRaisesRegex(_migrate_db.MigrationError, "Unsupported jobs"):
            _migrate_db.migrate_database(self.path)

    def test_cli_reports_deliberate_discard_and_backup(self) -> None:
        result = subprocess.run(
            [sys.executable, _migrate_remove_item_json.__file__, str(self.path)],
            text=True,
            capture_output=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("payload-only fields were removed", result.stdout)
        self.assertIn(str(self.backup), result.stdout)


if __name__ == "__main__":
    unittest.main()
