import json
import os
import shutil
import sqlite3
import subprocess
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from ats_models import (
    Job,
    JobRecord,
    Resume,
    ResumeRecord,
    application_from_row,
    job_from_row,
    job_to_row,
    resume_to_row,
)


PROJECT = Path(__file__).resolve().parents[1]


class CliIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.repo = self.root / "repo"
        (self.repo / "scripts").mkdir(parents=True)
        shutil.copyfile(PROJECT / "pyproject.toml", self.repo / "pyproject.toml")
        shutil.copyfile(
            PROJECT / "scripts/copy_one_job.sh",
            self.repo / "scripts/copy_one_job.sh",
        )
        self.home = self.root / "home"
        self.home.mkdir()
        self.database = self.root / "records.sqlite3"
        uv = shutil.which("uv")
        if uv is None:
            raise RuntimeError("The integration tests require uv")
        self.uv = uv
        self.env = {
            **os.environ,
            "HOME": str(self.home),
            "DB_PATH": str(self.database),
            "UV_PROJECT_ENVIRONMENT": str(PROJECT / ".venv"),
            "UV_NO_SYNC": "1",
            "PYTHONPATH": str(PROJECT / "src"),
        }
        self.seed(self.database)

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
            "technologies": {"backend": ["Python"], "custom": {"kept": True}},
            "skills": [["SQLite", "Testing"]],
            "positions": [{
                "company": "Example", "title": "Engineer", "dates": "2024",
                "responsibilities": "Build APIs",
                "contributions": [{"headline": "Built an API", "details": ["Tested"]}],
            }],
            "awards": [{"title": "Award", "description": "Details"}],
            "projects": [{"headline": "Project", "link": "https://example.com"}],
            "basis_for_resumes": ["original"],
            **extras,
        }

    def seed(self, path: Path, *, empty: bool = False) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(path)) as conn, conn:
            conn.execute("PRAGMA foreign_keys = ON")
            conn.executescript(
                (PROJECT / "src/ats_db/schema.sql").read_text(encoding="utf-8")
            )
            if empty:
                return
            for table, row in (
                ("jobs", job_to_row(JobRecord.model_validate(
                    self.job(id="job-a", entry_date="2024-01-01")
                )).model_dump()),
                ("jobs", job_to_row(JobRecord.model_validate(
                    self.job(id="job-b", title="Another engineer")
                )).model_dump()),
                ("resumes", resume_to_row(ResumeRecord.model_validate(
                    self.resume(id="resume-a", job="job-a", successful=1)
                )).model_dump()),
            ):
                conn.execute(
                    f"INSERT INTO {table} ({', '.join(row)}) "
                    f"VALUES ({', '.join('?' for _ in row)})",
                    tuple(row.values()),
                )

    def rows(self, table: str, database: Path | None = None) -> list[dict[str, object]]:
        if table not in {"jobs", "resumes", "applications"}:
            raise ValueError(table)
        path = database if database is not None else self.database
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as conn:
            conn.row_factory = sqlite3.Row
            order = "resume_id" if table == "applications" else "id"
            return [
                dict(row) for row in conn.execute(f"SELECT * FROM {table} ORDER BY {order}")
            ]

    def run_cli(
        self, resource: str, *args: str, data: str | None = None,
        database: Path | None = None, success: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            [self.uv, "run", "--no-sync", resource, *args],
            input=data, text=True, capture_output=True, timeout=30, cwd=self.repo,
            env={**self.env, "DB_PATH": str(database or self.database)},
        )
        if success:
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stderr, "")
        else:
            self.assertNotEqual(result.returncode, 0)
            self.assertTrue(result.stderr)
        return result

    def run_copy(
        self, source: Path | None, *, success: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        env = dict(self.env)
        if source is None:
            env.pop("DB_PATH")
        else:
            env["DB_PATH"] = str(source)
        result = subprocess.run(
            ["bash", str(self.repo / "scripts/copy_one_job.sh")],
            env=env, text=True, capture_output=True, timeout=30, cwd=self.root,
        )
        if success:
            self.assertEqual(result.returncode, 0, result.stderr)
        else:
            self.assertNotEqual(result.returncode, 0)
            self.assertTrue(result.stderr)
        return result

    def test_seeded_records_are_read_as_valid_nested_json(self) -> None:
        job = json.loads(self.run_cli("jobs", "job-a").stdout)
        resume = json.loads(self.run_cli("resumes", "resume-a").stdout)
        self.assertEqual(job["id"], "job-a")
        self.assertEqual(job["entry_date"], "2024-01-01")
        self.assertEqual(job["keywords"]["technologies"], ["Python"])
        self.assertEqual(resume["skills"], [["SQLite", "Testing"]])
        self.assertEqual(resume["positions"][0]["contributions"][0]["headline"], "Built an API")
        self.assertEqual(resume["technologies"]["custom"], {"kept": True})
        self.assertEqual(resume["successful"], 1)
        self.assertNotIn("item_json", job)

    def test_stdin_insert_read_delete_round_trip_for_both_resources(self) -> None:
        for resource, payload in (
            ("jobs", self.job(id="new-job")),
            ("resumes", self.resume(id="new-resume", job="new-job")),
        ):
            with self.subTest(resource=resource):
                self.run_cli(resource, "put", data=json.dumps(payload))
                stored = json.loads(self.run_cli(resource, str(payload["id"])).stdout)
                model = Job if resource == "jobs" else Resume
                expected = model.model_validate(payload).model_dump(mode="json")
                for name, value in expected.items():
                    self.assertEqual(stored[name], value)
                self.assertIsInstance(stored["entry_date"], str)
                self.assertIsInstance(stored["modified_date"], str)
        self.run_cli("resumes", "purge", "new-resume")
        self.run_cli("jobs", "purge", "new-job")
        self.assertEqual([row["id"] for row in self.rows("jobs")], ["job-a", "job-b"])
        self.assertEqual([row["id"] for row in self.rows("resumes")], ["resume-a"])
        self.run_cli("jobs", "new-job", success=False)
        self.run_cli("resumes", "new-resume", success=False)

    def test_argument_insert_and_generated_defaults_in_a_new_database(self) -> None:
        path = self.root / "fresh.sqlite3"
        self.run_cli("jobs", "put", json.dumps(self.job()), database=path)
        job = self.rows("jobs", path)[0]
        self.assertRegex(str(job["id"]), r"^[0-9a-f]{32}$")
        self.assertIsInstance(job["entry_date"], str)
        self.run_cli(
            "resumes", "put", data=json.dumps(self.resume(job=job["id"])), database=path,
        )
        resume = self.rows("resumes", path)[0]
        self.assertRegex(str(resume["id"]), r"^[0-9a-f]{32}$")
        self.assertEqual(resume["successful"], 0)
        self.assertEqual(json.loads(str(resume["basis_for_resumes"])), ["original"])

    def test_missing_reads_and_deletes_fail_without_changing_records(self) -> None:
        before = (self.rows("jobs"), self.rows("resumes"))
        for resource in ("jobs", "resumes"):
            for args in (("missing",), ("purge", "missing")):
                with self.subTest(resource=resource, args=args):
                    result = self.run_cli(resource, *args, success=False)
                    self.assertIn("not found", result.stderr.lower())
        self.assertEqual((self.rows("jobs"), self.rows("resumes")), before)

    def test_invalid_stdin_and_duplicate_inserts_do_not_persist(self) -> None:
        before = (self.rows("jobs"), self.rows("resumes"))
        for resource, inputs in (
            ("jobs", ("not JSON", "[]", json.dumps(self.job(title=123)),
                      json.dumps(self.job(id="job-a")),
                      json.dumps(self.job(custom={"unsupported": True})))),
            ("resumes", (json.dumps(self.resume(skills=123)),
                         json.dumps(self.resume(id="resume-a")))),
        ):
            for data in inputs:
                with self.subTest(resource=resource, data=data):
                    self.run_cli(resource, "put", data=data, success=False)
                    self.assertEqual((self.rows("jobs"), self.rows("resumes")), before)
        self.run_cli("jobs", "put", data=json.dumps(self.job(id="after-failure")))
        self.assertEqual(json.loads(self.run_cli("jobs", "after-failure").stdout)["id"],
                         "after-failure")

    def test_foreign_keys_block_orphans_and_protect_referenced_jobs(self) -> None:
        before = (self.rows("jobs"), self.rows("resumes"))
        self.run_cli("resumes", "put",
                     data=json.dumps(self.resume(id="orphan", job="missing")), success=False)
        self.run_cli("jobs", "purge", "job-a", success=False)
        self.assertEqual((self.rows("jobs"), self.rows("resumes")), before)
        self.run_cli("resumes", "purge", "resume-a")
        self.run_cli("jobs", "purge", "job-a")
        self.assertEqual([row["id"] for row in self.rows("jobs")], ["job-b"])
        self.assertEqual(self.rows("resumes"), [])

    def test_applications_view_tracks_real_cli_writes_and_deletes(self) -> None:
        self.run_cli("resumes", "put",
                     data=json.dumps(self.resume(id="resume-b", job="job-b", successful=True)))
        applications = [application_from_row(row) for row in self.rows("applications")]
        self.assertEqual([application.job_id for application in applications], ["job-a", "job-b"])
        self.assertEqual(applications[1].resume_item.skills, [["SQLite", "Testing"]])
        self.assertEqual(applications[1].successful, 1)
        self.run_cli("resumes", "purge", "resume-b")
        self.assertEqual(len(self.rows("applications")), 1)

    def test_record_ids_are_parameterized_across_cli_reads_and_deletes(self) -> None:
        before = self.rows("jobs")
        for args in (("' OR 1=1 --",), ("purge", "' OR 1=1 --")):
            self.run_cli("jobs", *args, success=False)
        self.assertEqual(self.rows("jobs"), before)

    def test_db_path_isolates_writes_from_other_databases(self) -> None:
        other = self.root / "other.sqlite3"
        self.seed(other)
        before = other.read_bytes()
        self.run_cli("jobs", "put", data=json.dumps(self.job(id="isolated-job")))
        self.assertEqual(other.read_bytes(), before)
        self.assertNotIn("isolated-job", [row["id"] for row in self.rows("jobs", other)])

    def test_copy_script_uses_db_path_and_preserves_source_and_destination_records(self) -> None:
        destination = self.repo / "test.db"
        self.seed(destination, empty=True)
        self.run_cli("jobs", "put", data=json.dumps(self.job(id="existing")), database=destination)
        before = self.database.read_bytes()
        self.run_copy(self.database)
        self.assertEqual(self.database.read_bytes(), before)
        self.assertEqual([row["id"] for row in self.rows("jobs", destination)], ["existing", "job-a"])
        original = job_from_row(self.rows("jobs")[0]).model_dump(mode="json")
        copied = json.loads(self.run_cli("jobs", "job-a", database=destination).stdout)
        self.assertEqual(copied, original)
        rows = self.rows("jobs", destination)
        self.run_copy(self.database, success=False)
        self.assertEqual(self.rows("jobs", destination), rows)
        self.assertEqual(self.database.read_bytes(), before)

    def test_copy_script_uses_the_default_location_when_db_path_is_unset(self) -> None:
        source = self.home / ".local/share/resumes/records.sqlite3"
        self.seed(source)
        before = source.read_bytes()
        self.run_copy(None)
        destination = self.repo / "test.db"
        self.assertEqual([row["id"] for row in self.rows("jobs", destination)], ["job-a"])
        self.assertEqual(source.read_bytes(), before)

    def test_copy_script_rejects_empty_and_missing_sources(self) -> None:
        empty = self.root / "empty.sqlite3"
        self.seed(empty, empty=True)
        destination = self.repo / "test.db"
        self.seed(destination, empty=True)
        before = empty.read_bytes()
        result = self.run_copy(empty, success=False)
        self.assertIn("source database has no jobs", result.stderr)
        self.assertEqual(empty.read_bytes(), before)
        self.assertEqual(self.rows("jobs", destination), [])
        missing = self.root / "missing.sqlite3"
        self.run_copy(missing, success=False)
        self.assertFalse(missing.exists())
        self.assertEqual(self.rows("jobs", destination), [])

    def test_copy_script_rejects_identical_symlinked_and_hardlinked_sources(self) -> None:
        destination = self.repo / "test.db"
        self.seed(destination)
        before = destination.read_bytes()
        symlink = self.root / "source-symlink.sqlite3"
        symlink.symlink_to(destination)
        hardlink = self.root / "source-hardlink.sqlite3"
        os.link(destination, hardlink)
        for source in (destination, symlink, hardlink):
            with self.subTest(source=source):
                result = self.run_copy(source, success=False)
                self.assertIn("source and destination databases must differ", result.stderr)
                self.assertEqual(destination.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
