ALTER TABLE automations ADD COLUMN last_result TEXT NOT NULL DEFAULT '';
ALTER TABLE automations ADD COLUMN last_chat_id INTEGER;
