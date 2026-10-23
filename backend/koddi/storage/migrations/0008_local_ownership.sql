ALTER TABLE chats ADD COLUMN owner_id TEXT NOT NULL DEFAULT '';
ALTER TABLE attachments ADD COLUMN owner_id TEXT NOT NULL DEFAULT '';
UPDATE chats SET owner_id=COALESCE((SELECT CAST(COALESCE(json_extract(value_json,'$.id'),json_extract(value_json,'$.user_id')) AS TEXT) FROM settings WHERE key='dokky_user'),'');
UPDATE attachments SET owner_id=COALESCE((SELECT CAST(COALESCE(json_extract(value_json,'$.id'),json_extract(value_json,'$.user_id')) AS TEXT) FROM settings WHERE key='dokky_user'),'');
CREATE INDEX chats_owner ON chats(owner_id,id);
CREATE INDEX attachments_owner ON attachments(owner_id,id);
