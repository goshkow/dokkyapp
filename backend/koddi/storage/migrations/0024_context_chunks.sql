-- фрагменты файлов для поиска контекста: границы и хеш файла; термы лежат в FTS5 context_fts (создаётся кодом)
CREATE TABLE IF NOT EXISTS context_chunks (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  path TEXT NOT NULL,
  start_line INTEGER NOT NULL,
  end_line INTEGER NOT NULL,
  symbol TEXT NOT NULL DEFAULT '',
  kind TEXT NOT NULL DEFAULT '',
  sha256 TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS context_chunks_path ON context_chunks(project_id, path);
