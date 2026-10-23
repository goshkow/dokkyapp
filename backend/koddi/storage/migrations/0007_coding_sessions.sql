CREATE TABLE IF NOT EXISTS coding_sessions (
 chat_id INTEGER PRIMARY KEY REFERENCES chats(id) ON DELETE CASCADE,
 user_id TEXT NOT NULL,
 session_json TEXT NOT NULL,
 messages_json TEXT NOT NULL DEFAULT '[]',
 tools_json TEXT NOT NULL DEFAULT '[]',
 history_json TEXT,
 updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS coding_session_keys (
 chat_id INTEGER PRIMARY KEY REFERENCES chats(id) ON DELETE CASCADE,
 user_id TEXT NOT NULL, request_key TEXT NOT NULL, body_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS coding_pending (
 chat_id INTEGER PRIMARY KEY REFERENCES chats(id) ON DELETE CASCADE,
 user_id TEXT NOT NULL, request_key TEXT NOT NULL, body_json TEXT NOT NULL,
 messages_json TEXT NOT NULL
);
