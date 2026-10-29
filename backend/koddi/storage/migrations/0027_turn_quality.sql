-- качество хода: контракт задачи, прогоны проверок, ревью и решение гейта перед завершением
CREATE TABLE IF NOT EXISTS turn_quality (
  turn_id INTEGER PRIMARY KEY REFERENCES turns(id) ON DELETE CASCADE,
  state TEXT NOT NULL DEFAULT 'implemented',
  contract_json TEXT NOT NULL DEFAULT '{}',
  evidence_json TEXT NOT NULL DEFAULT '[]',
  qa_json TEXT NOT NULL DEFAULT '{}',
  qa_runs INTEGER NOT NULL DEFAULT 0,
  qa_fingerprint TEXT,
  failure_signatures_json TEXT NOT NULL DEFAULT '[]',
  review_json TEXT NOT NULL DEFAULT '{}',
  review_rounds INTEGER NOT NULL DEFAULT 0,
  gate_blocks INTEGER NOT NULL DEFAULT 0,
  checkpoint_json TEXT NOT NULL DEFAULT '{}',
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
