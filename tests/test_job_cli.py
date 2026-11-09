import io
import json
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from ats_db import cli


class JobCliTests(unittest.TestCase):
    def test_put_reads_stdin_once_for_both_resources(self) -> None:
        for name in ("jobs", "resumes"):
            with self.subTest(name=name):
                resource = "job" if name == "jobs" else "resume"
                data = {"id": f"{resource}-1"}
                with (
                    patch("sys.argv", [f"/venv/bin/{name}", "put"]),
                    patch("sys.stdin") as stdin,
                    patch.object(cli, f"insert_{resource}") as insert,
                    redirect_stdout(io.StringIO()),
                ):
                    stdin.read.return_value = json.dumps(data)
                    getattr(cli, name)()
                stdin.read.assert_called_once_with()
                insert.assert_called_once_with(data)

    def test_put_accepts_a_json_argument_without_reading_stdin(self) -> None:
        with (
            patch("sys.argv", ["jobs", "put", '{"id": "job-1"}']),
            patch("sys.stdin") as stdin,
            patch.object(cli, "insert_job") as insert,
            redirect_stdout(io.StringIO()),
        ):
            cli.jobs()
        stdin.read.assert_not_called()
        insert.assert_called_once_with({"id": "job-1"})

    def test_purge_uses_the_id_after_the_subcommand(self) -> None:
        for name in ("jobs", "resumes"):
            with self.subTest(name=name):
                resource = "job" if name == "jobs" else "resume"
                with (
                    patch("sys.argv", [name, "purge", "record-1"]),
                    patch.object(cli, f"delete_{resource}") as delete,
                    redirect_stdout(io.StringIO()),
                ):
                    getattr(cli, name)()
                delete.assert_called_once_with("record-1")

    def test_bare_id_reads_still_print_json(self) -> None:
        output = io.StringIO()
        with (
            patch("sys.argv", ["jobs", "job-1"]),
            patch.object(cli, "read_job") as read,
            redirect_stdout(output),
        ):
            read.return_value.model_dump_json.return_value = '{"id": "job-1"}'
            cli.jobs()
        read.assert_called_once_with("job-1")
        self.assertEqual(json.loads(output.getvalue()), {"id": "job-1"})

    def test_put_rejects_non_object_json(self) -> None:
        with patch("sys.argv", ["jobs", "put", "[]"]):
            with self.assertRaisesRegex(ValueError, "JSON object"):
                cli.jobs()


if __name__ == "__main__":
    unittest.main()
