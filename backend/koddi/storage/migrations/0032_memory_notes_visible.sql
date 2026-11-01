-- заметки агента жили мимо настроек памяти: теперь это обычные пункты, их видно и можно удалить
UPDATE memory_summaries
SET kind = CASE WHEN project_id IS NULL THEN 'user' ELSE 'project' END,
    metadata_json = '{"source":"auto"}'
WHERE kind NOT IN ('user', 'project');
