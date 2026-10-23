ALTER TABLE agent_plans ADD COLUMN document_markdown TEXT NOT NULL DEFAULT '';
ALTER TABLE agent_plans ADD COLUMN execution_started_at TEXT;
UPDATE settings SET value_json='"ask"' WHERE key='permission_profile' AND value_json='"read-only"';
