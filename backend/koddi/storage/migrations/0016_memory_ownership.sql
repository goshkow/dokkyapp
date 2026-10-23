ALTER TABLE memory_summaries ADD COLUMN owner_id TEXT NOT NULL DEFAULT '';
UPDATE memory_summaries
 SET owner_id=COALESCE((SELECT CAST(COALESCE(json_extract(value_json,'$.id'),json_extract(value_json,'$.user_id')) AS TEXT)
                        FROM settings WHERE key='dokky_user'),'');
CREATE INDEX memory_owner_project ON memory_summaries(owner_id,project_id,id);
