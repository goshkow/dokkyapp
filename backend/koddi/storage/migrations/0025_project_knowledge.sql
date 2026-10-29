-- знания проекта поверх заметок карты: происхождение, уверенность, статус и отпечаток версии кода
ALTER TABLE project_map_notes ADD COLUMN source TEXT NOT NULL DEFAULT 'agent';
ALTER TABLE project_map_notes ADD COLUMN confidence TEXT NOT NULL DEFAULT 'medium';
ALTER TABLE project_map_notes ADD COLUMN status TEXT NOT NULL DEFAULT 'valid';
ALTER TABLE project_map_notes ADD COLUMN basis TEXT NOT NULL DEFAULT '';
ALTER TABLE project_map_notes ADD COLUMN fingerprint TEXT NOT NULL DEFAULT '';

-- последний выгруженный в Markdown вид заметки: правка файла вне Докки распознаётся по хешу
CREATE TABLE IF NOT EXISTS knowledge_exports (
  project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  owner_id TEXT NOT NULL DEFAULT '',
  note_id INTEGER NOT NULL,
  location TEXT NOT NULL,
  path TEXT NOT NULL,
  sha256 TEXT NOT NULL,
  exported_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (project_id, owner_id, location, note_id)
);
