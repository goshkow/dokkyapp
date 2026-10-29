-- плагины с GitHub по аккаунту: файлы лежат в папке данных, здесь коммит, состав и хэши файлов
CREATE TABLE IF NOT EXISTS plugins (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  owner_id TEXT NOT NULL,
  name TEXT NOT NULL,
  title TEXT NOT NULL DEFAULT '',
  description TEXT NOT NULL DEFAULT '',
  version TEXT NOT NULL DEFAULT '',
  repo TEXT NOT NULL,
  path TEXT NOT NULL DEFAULT '',
  ref TEXT NOT NULL DEFAULT '',
  commit_sha TEXT NOT NULL,
  tree_sha TEXT NOT NULL,
  catalog_id TEXT,
  license TEXT NOT NULL DEFAULT '',
  manifest_json TEXT NOT NULL DEFAULT '{}',
  files_json TEXT NOT NULL DEFAULT '{}',
  dir TEXT NOT NULL,
  enabled INTEGER NOT NULL DEFAULT 1,
  -- хуки и MCP исполняются, только если человек разрешил именно этот набор (exec_approved = exec_digest)
  exec_digest TEXT NOT NULL DEFAULT '',
  exec_approved TEXT,
  installed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE (owner_id, name)
);

-- вывод хука SessionStart: считается один раз на чат и дальше идёт в контекст каждого хода
CREATE TABLE IF NOT EXISTS plugin_hook_outputs (
  plugin_id INTEGER NOT NULL REFERENCES plugins(id) ON DELETE CASCADE,
  chat_id INTEGER NOT NULL,
  digest TEXT NOT NULL,
  output TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (plugin_id, chat_id)
);
