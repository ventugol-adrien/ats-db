CREATE TABLE IF NOT EXISTS jobs (
	id TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
	entry_date TEXT DEFAULT CURRENT_TIMESTAMP,
	modified_date TEXT DEFAULT CURRENT_TIMESTAMP,
	company TEXT,
	favicon TEXT,
	title TEXT,
	link TEXT,
	description TEXT,
	keywords TEXT CHECK(keywords IS NULL OR (json_valid(keywords) AND json_type(keywords) = 'object')),
	resume TEXT DEFAULT NULL
);

CREATE TABLE IF NOT EXISTS resumes (
	id TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
	entry_date TEXT DEFAULT CURRENT_TIMESTAMP,
	modified_date TEXT DEFAULT CURRENT_TIMESTAMP,
	technologies TEXT CHECK(technologies IS NULL OR (json_valid(technologies) AND json_type(technologies) = 'object')),
	skills TEXT CHECK(skills IS NULL OR (json_valid(skills) AND json_type(skills) = 'array')),
	responsibilities TEXT,
	positions TEXT CHECK(positions IS NULL OR (json_valid(positions) AND json_type(positions) = 'array')),
	checklist TEXT CHECK(checklist IS NULL OR (json_valid(checklist) AND json_type(checklist) = 'array')),
	education TEXT CHECK(education IS NULL OR (json_valid(education) AND json_type(education) = 'object')),
	languages TEXT CHECK(languages IS NULL OR (json_valid(languages) AND json_type(languages) = 'array')),
	awards TEXT CHECK(awards IS NULL OR (json_valid(awards) AND json_type(awards) = 'array')),
	projects TEXT CHECK(projects IS NULL OR (json_valid(projects) AND json_type(projects) = 'array')),
	contact TEXT CHECK(contact IS NULL OR (json_valid(contact) AND json_type(contact) = 'object')),
	job TEXT REFERENCES jobs(id),
	basis_for_resumes TEXT CHECK(basis_for_resumes IS NULL OR json_valid(basis_for_resumes)),
	successful INTEGER DEFAULT (0)
);

DROP VIEW IF EXISTS applications;
CREATE VIEW applications AS
SELECT
	r.successful AS successful,
	j.id AS job_id,
    r.id AS resume_id,
    json_object(
        'id', j.id, 'entry_date', j.entry_date, 'modified_date', j.modified_date,
        'company', j.company, 'favicon', j.favicon, 'title', j.title,
        'link', j.link, 'description', j.description,
        'keywords', json(j.keywords), 'resume', j.resume
    ) AS job_item,
    json_object(
        'id', r.id, 'entry_date', r.entry_date, 'modified_date', r.modified_date,
        'technologies', json(r.technologies), 'skills', json(r.skills),
        'responsibilities', r.responsibilities, 'positions', json(r.positions),
        'checklist', json(r.checklist), 'education', json(r.education),
        'languages', json(r.languages), 'awards', json(r.awards),
        'projects', json(r.projects), 'contact', json(r.contact),
        'job', r.job, 'basis_for_resumes', json(r.basis_for_resumes),
        'successful', r.successful
    ) AS resume_item
FROM jobs j
JOIN resumes r ON j.id = r.job;

-- Trigger to update modified_date on jobs, resumes
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