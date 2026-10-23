ALTER TABLE project_grants ADD COLUMN approval_profile TEXT
 CHECK(approval_profile IS NULL OR approval_profile IN ('ask','trusted','allow-all'));
INSERT OR IGNORE INTO settings(key,value_json)
 SELECT 'coding_preferences_legacy_owner', json_quote(CAST(COALESCE(json_extract(value_json,'$.id'),json_extract(value_json,'$.user_id'),'') AS TEXT))
 FROM settings WHERE key='dokky_user';
