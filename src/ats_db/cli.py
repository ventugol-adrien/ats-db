import argparse, sys, os
import json
from ats_db.resumes import read_resume, insert_resume, delete_resume
from ats_db.jobs import read_job, insert_job, delete_job


def _input_payload() -> dict[str, object]:
    data = json.loads(sys.argv[2]) if len(sys.argv) > 2 else json.load(sys.stdin)
    if not isinstance(data, dict):
        raise ValueError("Expected a JSON object")
    return data


def jobs():
    if sys.argv[1] == "put":
        job_data = _input_payload()
        print(insert_job(job_data))
    elif sys.argv[1] == "purge":
        print(delete_job(sys.argv[2]))
    else:
        print(read_job(sys.argv[1]).model_dump_json())


def resumes():
    if sys.argv[1] == "put":
        resume_data = _input_payload()
        print(insert_resume(resume_data))
    elif sys.argv[1] == "purge":
        print(delete_resume(sys.argv[2]))
    else:
        print(read_resume(sys.argv[1]).model_dump_json())
