import os
from collections.abc import Mapping
from pathlib import Path
from functools import wraps
from typing import Literal, Callable, Concatenate
import sqlite3

from ats_models import JobRecord, JobRow, ResumeRow

type SQLValue = str | int | float | bytes | None
type Table = Literal["jobs", "resumes"]

TABLE_COLUMNS = {
    "jobs": frozenset(JobRow.model_fields),
    "resumes": frozenset(ResumeRow.model_fields),
}

DB = Path(
    os.getenv(
        "ATS_DB_PATH", Path.home() / ".local" / "share" / "resumes" / "records.sqlite3"
    )
)
CONNECTION: sqlite3.Connection | None = None
CURSOR: sqlite3.Cursor | None = None


def get_connection() -> tuple[sqlite3.Connection, sqlite3.Cursor]:
    global CONNECTION, CURSOR
    if CONNECTION is None:
        CONNECTION = sqlite3.connect(DB)
        CONNECTION.row_factory = sqlite3.Row
        CONNECTION.execute("PRAGMA foreign_keys = ON")
    if CURSOR is None:
        CURSOR = CONNECTION.cursor()
    return CONNECTION, CURSOR


def close_connection() -> None:
    global CONNECTION, CURSOR
    conn, cursor = CONNECTION, CURSOR
    CONNECTION = None
    CURSOR = None
    try:
        if cursor is not None:
            cursor.close()
    finally:
        if conn is not None:
            conn.close()


def ensure_schema(conn: sqlite3.Connection) -> None:
    sql = Path(__file__).with_name("schema.sql").read_text(encoding="utf-8")
    conn.executescript(sql)


def validate_table(table: str, *, allow_applications: bool = False) -> None:
    if table not in TABLE_COLUMNS and not (
        allow_applications and table == "applications"
    ):
        raise ValueError(f"Unsupported table: {table!r}")


def db_op[**P, R](
    func: Callable[Concatenate[sqlite3.Connection, sqlite3.Cursor, P], R],
) -> Callable[P, R]:
    @wraps(func)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        try:
            conn, cursor = get_connection()
            ensure_schema(conn)
            with conn:
                return func(conn, cursor, *args, **kwargs)
        finally:
            close_connection()

    return wrapper


@db_op
def read_record(
    conn: sqlite3.Connection,
    cursor: sqlite3.Cursor,
    table: Literal["jobs", "resumes", "applications"],
    record_id: str,
) -> dict[str, SQLValue] | None:
    validate_table(table, allow_applications=True)
    cursor.execute(f"SELECT * FROM {table} WHERE id = ?", (record_id,))
    record = cursor.fetchone()
    return dict(record) if record else None


@db_op
def insert_record(
    conn: sqlite3.Connection,
    cursor: sqlite3.Cursor,
    table: Table,
    record: Mapping[str, SQLValue],
) -> str:
    validate_table(table)
    unknown = record.keys() - TABLE_COLUMNS[table]
    if unknown:
        raise ValueError(f"Unsupported columns for {table}: {sorted(unknown)!r}")
    if not record:
        raise ValueError("Cannot insert an empty record")
    columns = ", ".join(f'"{column}"' for column in record)
    placeholders = ", ".join("?" for _ in record)
    values = tuple(record.values())
    cursor.execute(
        f"INSERT INTO {table} ({columns}) VALUES ({placeholders})",
        values,
    )
    cursor.execute("SELECT last_insert_rowid()")
    last_id = cursor.fetchone()[0]
    return last_id


@db_op
def delete_record(
    conn: sqlite3.Connection,
    cursor: sqlite3.Cursor,
    table: Table,
    record_id: str,
) -> None:
    validate_table(table)
    cursor.execute(f"DELETE FROM {table} WHERE id = ?", (record_id,))
    cursor.execute("SELECT changes()")
    changes = cursor.fetchone()[0]
    if changes == 0:
        raise LookupError(f"Record not found: {record_id!r}")
    return changes


def read_job(job_id: str) -> JobRecord:
    from ats_db.jobs import read_job as read_job_record

    return read_job_record(job_id)
