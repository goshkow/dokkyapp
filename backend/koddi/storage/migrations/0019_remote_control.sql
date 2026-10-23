-- доступ с телефона: компьютер зеркалит чаты Докки Code на сервер и забирает оттуда команды
CREATE TABLE IF NOT EXISTS remote_control (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  enabled INTEGER NOT NULL DEFAULT 0,
  install_id TEXT NOT NULL,
  account_id TEXT NOT NULL DEFAULT '',
  host_id TEXT,
  name TEXT NOT NULL DEFAULT '',
  prevent_sleep TEXT NOT NULL DEFAULT 'working' CHECK (prevent_sleep IN ('off', 'working', 'on_ac')),
  new_session_project_id INTEGER
);

CREATE TABLE IF NOT EXISTS remote_sessions (
  chat_id INTEGER PRIMARY KEY,
  session_key TEXT NOT NULL UNIQUE,
  public_chat_id INTEGER,
  shared INTEGER NOT NULL DEFAULT 1,
  last_client_seq INTEGER NOT NULL DEFAULT 0,
  history_synced INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS remote_outbox (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_key TEXT NOT NULL,
  kind TEXT NOT NULL CHECK (kind IN ('message', 'event', 'session', 'close')),
  body TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS remote_outbox_session ON remote_outbox(session_key, id);

CREATE TABLE IF NOT EXISTS remote_commands_seen (
  id TEXT PRIMARY KEY,
  status TEXT NOT NULL,
  error TEXT NOT NULL DEFAULT '',
  seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
