-- вложения, которые уже ушли на сервер для показа на телефоне: при каждой сверке их не перечитываем
CREATE TABLE IF NOT EXISTS remote_attachments_sent (
  attachment_id INTEGER PRIMARY KEY
);

-- новый вид записи в очереди отправки: ограничение таблицы меняется только её пересозданием
CREATE TABLE remote_outbox_new (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_key TEXT NOT NULL,
  kind TEXT NOT NULL CHECK (kind IN ('message', 'event', 'session', 'close', 'attachment')),
  body TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
INSERT INTO remote_outbox_new(id, session_key, kind, body, created_at)
  SELECT id, session_key, kind, body, created_at FROM remote_outbox;
DROP TABLE remote_outbox;
ALTER TABLE remote_outbox_new RENAME TO remote_outbox;
CREATE INDEX IF NOT EXISTS remote_outbox_session ON remote_outbox(session_key, id);
