-- объективные гейты качества: прогоны, снимок долга проекта, кэш мутаций, архитектурные предложения и журнал решений
CREATE TABLE IF NOT EXISTS quality_runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  owner_id TEXT NOT NULL DEFAULT '',
  turn_id INTEGER REFERENCES turns(id) ON DELETE SET NULL,
  gate TEXT NOT NULL,
  status TEXT NOT NULL,
  idempotency_key TEXT NOT NULL,
  source_revision TEXT NOT NULL DEFAULT '',
  baseline_revision TEXT,
  scope_json TEXT NOT NULL DEFAULT '[]',
  checker_json TEXT NOT NULL DEFAULT '{}',
  findings_json TEXT NOT NULL DEFAULT '[]',
  summary_json TEXT NOT NULL DEFAULT '{}',
  report_path TEXT,
  error TEXT,
  policy_version INTEGER NOT NULL DEFAULT 1,
  started_at TEXT NOT NULL,
  duration_ms INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE (project_id, owner_id, idempotency_key)
);
CREATE INDEX IF NOT EXISTS quality_runs_project ON quality_runs(project_id, owner_id, gate, id);
CREATE INDEX IF NOT EXISTS quality_runs_turn ON quality_runs(turn_id);

CREATE TABLE IF NOT EXISTS quality_baseline (
  project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  owner_id TEXT NOT NULL DEFAULT '',
  gate TEXT NOT NULL,
  item_key TEXT NOT NULL,
  path TEXT NOT NULL,
  body_hash TEXT NOT NULL,
  metrics_json TEXT NOT NULL DEFAULT '{}',
  decision TEXT NOT NULL DEFAULT 'review',
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (project_id, owner_id, gate, item_key)
);
CREATE INDEX IF NOT EXISTS quality_baseline_path ON quality_baseline(project_id, owner_id, path);

CREATE TABLE IF NOT EXISTS quality_cache (
  cache_key TEXT PRIMARY KEY,
  value_json TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS architecture_proposals (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  owner_id TEXT NOT NULL DEFAULT '',
  name TEXT NOT NULL,
  assignments_json TEXT NOT NULL DEFAULT '{}',
  note TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS agent_decisions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  chat_id INTEGER NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
  turn_id INTEGER REFERENCES turns(id) ON DELETE SET NULL,
  project_id INTEGER,
  kind TEXT NOT NULL,
  summary TEXT NOT NULL,
  refs_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS agent_decisions_chat ON agent_decisions(chat_id, id);

ALTER TABLE turn_quality ADD COLUMN workflow_json TEXT NOT NULL DEFAULT '{}';
