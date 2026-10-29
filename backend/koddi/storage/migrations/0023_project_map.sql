-- карта проекта: разобранные файлы, состояние сканирования и заметки агента
CREATE TABLE IF NOT EXISTS project_map_files (
  project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  path TEXT NOT NULL,
  size INTEGER NOT NULL,
  mtime_ns INTEGER NOT NULL,
  sha256 TEXT NOT NULL,
  lang TEXT NOT NULL,
  info_json TEXT NOT NULL DEFAULT '{}',
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (project_id, path)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS project_map_state (
  project_id INTEGER PRIMARY KEY REFERENCES projects(id) ON DELETE CASCADE,
  version INTEGER NOT NULL DEFAULT 0,
  parser_version INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'idle',
  error TEXT,
  files INTEGER NOT NULL DEFAULT 0,
  truncated INTEGER NOT NULL DEFAULT 0,
  built_at TEXT,
  scanned_at TEXT
);

-- заметки принадлежат аккаунту, как память проекта, и переходят с проектом при входе другим аккаунтом
CREATE TABLE IF NOT EXISTS project_map_notes (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  owner_id TEXT NOT NULL DEFAULT '',
  kind TEXT NOT NULL,
  title TEXT NOT NULL,
  body TEXT NOT NULL DEFAULT '',
  targets_json TEXT NOT NULL DEFAULT '[]',
  chat_id INTEGER,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE UNIQUE INDEX IF NOT EXISTS project_map_notes_title ON project_map_notes(project_id, owner_id, title);
