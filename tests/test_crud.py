import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pydantic import ValidationError
from ats_models import application_from_row

from ats_db import db
from ats_db.jobs import delete_job, insert_job, read_job
from ats_db.resumes import delete_resume, insert_resume, read_resume


class CrudTests(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "records.sqlite3"
        db.close_connection()
        patcher = patch.object(db, "DB", self.path)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(db.close_connection)
        conn, _ = db.get_connection()
        db.ensure_schema(conn)
        db.close_connection()

    def job(self, **extras: object) -> dict[str, object]:
        return {
            "company": "Example",
            "title": "Engineer",
            "description": "Build APIs",
            "keywords": {"technologies": ["Python"]},
            **extras,
        }

    def resume(self, **extras: object) -> dict[str, object]:
        return {
            "technologies": {"backend": ["Python"]},
            "skills": [["SQLite"]],
            "positions": [{
                "company": "Example",
                "title": "Engineer",
                "dates": "2024",
                "responsibilities": "Build APIs",
                "contributions": [{"headline": "Built an API", "details": ["Tested"]}],
            }],
            "awards": [{"title": "Award", "description": "Details"}],
            "projects": [{"headline": "Project", "link": "https://example.com"}],
            **extras,
        }

    def row(self, table: str) -> dict[str, object]:
        if table not in {"jobs", "resumes"}:
            raise ValueError(table)
        with sqlite3.connect(self.path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(f"SELECT * FROM {table}").fetchone()
            self.assertIsNotNone(row)
            return dict(row)

    def assert_closed(self) -> None:
        self.assertIsNone(db.CONNECTION)
        self.assertIsNone(db.CURSOR)

    def test_initialization_is_repeatable_and_closes_connection(self) -> None:
        db.read_record("jobs", "missing")
        self.assert_closed()
        with sqlite3.connect(self.path) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0], 0)

    def test_job_round_trip_with_generated_metadata(self) -> None:
        job_id = insert_job(self.job())
        self.assert_closed()
        row = self.row("jobs")
        self.assertEqual(job_id, row["id"])
        self.assertRegex(row["id"], r"^[0-9a-f]{32}$")
        self.assertIsInstance(row["entry_date"], str)
        self.assertIsInstance(row["modified_date"], str)
        self.assertIsInstance(row["keywords"], str)
        self.assertEqual(json.loads(row["keywords"])["technologies"], ["Python"])
        self.assertNotIn("item_json", row)
        record = read_job(row["id"])
        self.assertEqual(record.id, row["id"])
        self.assertEqual(record.entry_date, row["entry_date"])
        self.assertEqual(record.keywords.technologies, ["Python"])
        self.assertEqual(db.read_job(row["id"]), record)
        self.assert_closed()

    def test_resume_round_trip_with_nested_values_and_defaults(self) -> None:
        resume_id = insert_resume(self.resume())
        row = self.row("resumes")
        self.assertEqual(resume_id, row["id"])
        self.assertRegex(row["id"], r"^[0-9a-f]{32}$")
        self.assertEqual(row["successful"], 0)
        self.assertIsInstance(row["positions"], str)
        self.assertEqual(json.loads(row["skills"]), [["SQLite"]])
        record = read_resume(row["id"])
        self.assertEqual(record.id, row["id"])
        self.assertEqual(record.successful, 0)
        self.assertEqual(record.technologies.backend, ["Python"])
        self.assertEqual(record.positions[0].contributions[0].headline, "Built an API")
        self.assert_closed()

    def test_insert_returns_explicit_text_ids(self) -> None:
        self.assertEqual(insert_job(self.job(id="job-explicit")), "job-explicit")
        self.assertEqual(
            insert_resume(self.resume(id="resume-explicit", job="job-explicit")),
            "resume-explicit",
        )

    def test_explicit_null_id_remains_null(self) -> None:
        self.assertIsNone(insert_job(self.job(id=None)))
        self.assertIsNone(self.row("jobs")["id"])

    def test_metadata_updates_need_no_json_copies(self) -> None:
        insert_job(self.job(id="job-1", modified_date="2000-01-01"))
        insert_resume(self.resume(id="resume-1", modified_date="2000-01-01"))
        with sqlite3.connect(self.path) as conn:
            conn.execute("UPDATE jobs SET title = title WHERE id = 'job-1'")
            conn.execute(
                "UPDATE resumes SET responsibilities = responsibilities "
                "WHERE id = 'resume-1'"
            )
        for table, read in (("jobs", read_job), ("resumes", read_resume)):
            with self.subTest(table=table):
                row = self.row(table)
                self.assertNotIn("item_json", row)
                self.assertNotEqual(row["modified_date"], "2000-01-01")
                record = read(row["id"])
                self.assertEqual(record.modified_date, row["modified_date"])

    def test_application_view_needs_no_json_metadata_copies(self) -> None:
        insert_job(self.job(id="job-1"))
        insert_resume(self.resume(id="resume-1", job="job-1"))
        with sqlite3.connect(self.path) as conn:
            conn.row_factory = sqlite3.Row
            row = dict(conn.execute("SELECT * FROM applications").fetchone())
        application = application_from_row(row)
        self.assertEqual(application.job_item.id, "job-1")
        self.assertEqual(application.resume_item.id, "resume-1")
        self.assertEqual(application.resume_item.successful, 0)

    def test_sql_content_updates_are_authoritative(self) -> None:
        insert_job(self.job(id="job-1"))
        with sqlite3.connect(self.path) as conn:
            conn.execute("UPDATE jobs SET title = 'Different' WHERE id = 'job-1'")
        self.assertEqual(read_job("job-1").title, "Different")

    def test_explicit_metadata_and_defaults_are_preserved(self) -> None:
        insert_resume(self.resume(
            id="resume-1", entry_date="2024-01-01", successful=True,
            basis_for_resumes=["original"],
        ))
        row = self.row("resumes")
        self.assertEqual(row["id"], "resume-1")
        self.assertEqual(row["entry_date"], "2024-01-01")
        self.assertEqual(row["successful"], 1)
        record = read_resume("resume-1")
        self.assertEqual(record.basis_for_resumes, ["original"])
        self.assertEqual(record.successful, 1)

    def test_explicit_null_does_not_activate_default(self) -> None:
        insert_resume(self.resume(id="resume-1", successful=None))
        self.assertIsNone(self.row("resumes")["successful"])
        self.assertIsNone(read_resume("resume-1").successful)

    def test_missing_records_raise_lookup_error(self) -> None:
        for read in (read_job, read_resume, db.read_job):
            with self.subTest(read=read), self.assertRaises(LookupError):
                read("missing")
            self.assert_closed()

    def test_low_level_missing_record_returns_none(self) -> None:
        self.assertIsNone(db.read_record("jobs", "missing"))
        self.assert_closed()

    def test_foreign_keys_block_orphans_and_referenced_job_deletion(self) -> None:
        with self.assertRaises(sqlite3.IntegrityError):
            insert_resume(self.resume(id="orphan", job="missing"))
        self.assert_closed()
        self.assertIsNone(db.read_record("resumes", "orphan"))
        insert_job(self.job(id="job-1"))
        insert_resume(self.resume(id="resume-1", job="job-1"))
        with self.assertRaises(sqlite3.IntegrityError):
            delete_job("job-1")
        self.assert_closed()
        self.assertEqual(read_job("job-1").id, "job-1")
        delete_resume("resume-1")
        delete_job("job-1")
        self.assertIsNone(db.read_record("jobs", "job-1"))
        self.assertIsNone(db.read_record("resumes", "resume-1"))

    def test_foreign_keys_enabled_on_each_connection(self) -> None:
        for _ in range(2):
            conn, _ = db.get_connection()
            self.assertEqual(conn.execute("PRAGMA foreign_keys").fetchone()[0], 1)
            db.close_connection()

    def test_invalid_table_identifiers_are_rejected(self) -> None:
        for table in ("jobs; DROP TABLE resumes; --", "unknown"):
            for operation in (
                lambda: db.read_record(table, "id"),
                lambda: db.insert_record(table, {"keywords": "{}"}),
                lambda: db.delete_record(table, "id"),
            ):
                with self.subTest(table=table), self.assertRaises(ValueError):
                    operation()
                self.assert_closed()

    def test_invalid_columns_and_empty_records_are_rejected(self) -> None:
        for record in (
            {},
            {"keywords": "{}", "unknown": "value"},
            {"keywords": "{}", 'id") VALUES (?); --': "value"},
            {"item_json": "{}"},
        ):
            with self.subTest(record=record), self.assertRaises(ValueError):
                db.insert_record("jobs", record)
            self.assert_closed()

    def test_extra_payload_fields_are_rejected(self) -> None:
        extra = 'custom"); DROP TABLE jobs; --'
        with self.assertRaisesRegex(ValueError, "SQL columns"):
            insert_job(self.job(id="job-1", **{extra: {"kept": True}}))
        self.assertIsNone(db.read_record("jobs", "job-1"))

    def test_nested_json_extra_fields_are_preserved(self) -> None:
        insert_resume(self.resume(
            id="resume-1",
            technologies={"backend": ["Python"], "custom": {"kept": True}},
        ))
        record = read_resume("resume-1")
        self.assertEqual(record.technologies.model_extra["custom"], {"kept": True})

    def test_record_ids_are_parameterized(self) -> None:
        insert_job(self.job(id="job-1"))
        malicious_id = "' OR 1=1 --"
        self.assertIsNone(db.read_record("jobs", malicious_id))
        with self.assertRaises(LookupError):
            delete_job(malicious_id)
        self.assertEqual(read_job("job-1").id, "job-1")

    def test_invalid_content_is_rejected_before_insert(self) -> None:
        with self.assertRaises(ValidationError):
            insert_job(self.job(title=123))
        with self.assertRaises(ValidationError):
            insert_resume(self.resume(skills=123))
        with sqlite3.connect(self.path) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM resumes").fetchone()[0], 0)

    def test_failed_insert_rolls_back_and_connection_can_be_reused(self) -> None:
        insert_job(self.job(id="job-1"))
        with self.assertRaises(sqlite3.IntegrityError):
            insert_job(self.job(id="job-1"))
        self.assert_closed()
        insert_job(self.job(id="job-2"))
        self.assertEqual(read_job("job-2").id, "job-2")

    def test_invalid_json_payload_is_rejected_by_database(self) -> None:
        with self.assertRaises(sqlite3.DatabaseError):
            db.insert_record("jobs", {"keywords": "[]"})
        self.assert_closed()

    def test_json_provenance_scalars_round_trip(self) -> None:
        for index, value in enumerate(("source-id", 42, False, ["original"], {"id": "source"})):
            resume_id = f"resume-{index}"
            insert_resume(self.resume(id=resume_id, basis_for_resumes=value))
            self.assertEqual(read_resume(resume_id).basis_for_resumes, value)


if __name__ == "__main__":
    unittest.main()
