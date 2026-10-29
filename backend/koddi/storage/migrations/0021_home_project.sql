-- служебный проект чатов без проекта (папка «Докки» в документах): в списке проектов не показывается
ALTER TABLE projects ADD COLUMN hidden INTEGER NOT NULL DEFAULT 0;
