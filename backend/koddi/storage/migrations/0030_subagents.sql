-- чат сабагента принадлежит чату, где его запустили: в списке чатов его нет, удаляется вместе с родителем
ALTER TABLE chats ADD COLUMN parent_chat_id INTEGER REFERENCES chats(id) ON DELETE CASCADE;
CREATE INDEX IF NOT EXISTS chats_parent ON chats(parent_chat_id);

-- вывод SessionStart хуков проекта: один запуск на чат и версию файла настроек
CREATE TABLE IF NOT EXISTS project_hook_outputs (
  chat_id INTEGER NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
  path TEXT NOT NULL,
  digest TEXT NOT NULL,
  output TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (chat_id, path)
);
