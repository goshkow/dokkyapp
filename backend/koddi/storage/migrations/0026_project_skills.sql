-- навыки из SKILL.md в папке проекта: используются только после явного подтверждения человеком
CREATE TABLE IF NOT EXISTS project_skills (
  project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  owner_id TEXT NOT NULL DEFAULT '',
  path TEXT NOT NULL,
  name TEXT NOT NULL,
  description TEXT NOT NULL DEFAULT '',
  sha256 TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending',
  trusted_sha256 TEXT,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (project_id, owner_id, path)
);
