from collections.abc import Mapping

from ats_models import (
    Resume,
    ResumeRecord,
    parse_resume_payload,
    resume_from_row,
    resume_to_row,
)

from ats_db.db import read_record, insert_record, delete_record


def read_resume(resume_id: str) -> ResumeRecord:
    row = read_record("resumes", resume_id)
    if row is None:
        raise LookupError(f"Resume not found: {resume_id!r}")
    return resume_from_row(row)


def insert_resume(resume: Mapping[str, object]) -> str | None:
    payload = Resume.model_validate(dict(resume)).model_dump(mode="json")
    record = parse_resume_payload(payload)
    row = resume_to_row(record).model_dump(
        exclude={"id", "entry_date", "modified_date", "successful"} - payload.keys()
    )
    new_id = insert_record("resumes", row)
    return new_id


def delete_resume(resume_id: str) -> None:
    delete_record("resumes", resume_id)
