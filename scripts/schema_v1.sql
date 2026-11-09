CREATE TABLE IF NOT EXISTS jobs (
	id TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
	entry_date TEXT DEFAULT CURRENT_TIMESTAMP,
	modified_date TEXT DEFAULT CURRENT_TIMESTAMP,
	company TEXT,
	favicon TEXT,
	title TEXT,
	link TEXT,
	description TEXT,
	keywords TEXT,
	resume TEXT DEFAULT NULL,
	item_json TEXT NOT NULL CHECK(json_valid(item_json) AND json_type(item_json) = 'object')
);

CREATE TABLE IF NOT EXISTS resumes (
	id TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
	entry_date TEXT DEFAULT CURRENT_TIMESTAMP,
	modified_date TEXT DEFAULT CURRENT_TIMESTAMP,
	technologies TEXT,
	skills TEXT,
	responsibilities TEXT,
	positions TEXT,
	checklist TEXT,
	education TEXT,
	languages TEXT,
	awards TEXT,
	projects TEXT,
	contact TEXT,
	job TEXT REFERENCES jobs(id),
	basis_for_resumes TEXT,
	successful INTEGER DEFAULT (0),
	item_json TEXT NOT NULL CHECK(json_valid(item_json) AND json_type(item_json) = 'object')
);

DROP VIEW IF EXISTS applications;
CREATE VIEW applications AS
SELECT
	r.successful AS successful,
	j.id AS job_id,
    r.id AS resume_id,
    j.item_json AS job_item,
    r.item_json AS resume_item
FROM jobs j
JOIN resumes r ON j.id = r.job;

CREATE TRIGGER IF NOT EXISTS update_jobs_modified_date
AFTER UPDATE ON jobs
FOR EACH ROW
BEGIN
    UPDATE jobs SET modified_date = CURRENT_TIMESTAMP WHERE id = OLD.id;
END;

CREATE TRIGGER IF NOT EXISTS update_resumes_modified_date
AFTER UPDATE ON resumes
FOR EACH ROW
BEGIN
    UPDATE resumes SET modified_date = CURRENT_TIMESTAMP WHERE id = OLD.id;
END;
