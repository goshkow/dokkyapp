ALTER TABLE logs ADD COLUMN owner_id TEXT NOT NULL DEFAULT '';
ALTER TABLE change_sets ADD COLUMN owner_id TEXT NOT NULL DEFAULT '';
UPDATE logs SET owner_id=COALESCE((SELECT CAST(COALESCE(json_extract(value_json,'$.id'),json_extract(value_json,'$.user_id')) AS TEXT)
                                 FROM settings WHERE key='dokky_user'),'');
UPDATE change_sets SET owner_id=COALESCE(
 (SELECT c.owner_id FROM turns t JOIN chats c ON c.id=t.chat_id WHERE t.id=change_sets.turn_id),
 (SELECT CAST(COALESCE(json_extract(value_json,'$.id'),json_extract(value_json,'$.user_id')) AS TEXT)
  FROM settings WHERE key='dokky_user'),'');
CREATE INDEX logs_owner ON logs(owner_id,id);
CREATE INDEX changes_owner_project ON change_sets(owner_id,project_id,id);
