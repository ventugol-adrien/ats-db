from collections.abc import Mapping

from ats_models import (
    Job,
    JobRecord,
    job_from_row,
    job_to_row,
    parse_job_payload,
)

from ats_db.db import read_record, insert_record, delete_record


def read_job(job_id: str) -> JobRecord:
    row = read_record("jobs", job_id)
    if row is None:
        raise LookupError(f"Job not found: {job_id!r}")
    return job_from_row(row)


def insert_job(job: Mapping[str, object]) -> None:
    payload = Job.model_validate(dict(job)).model_dump(mode="json")
    record = parse_job_payload(payload)
    row = job_to_row(record).model_dump(
        exclude={"id", "entry_date", "modified_date"} - payload.keys()
    )
    insert_record("jobs", row)


def delete_job(job_id: str) -> None:
    delete_record("jobs", job_id)
