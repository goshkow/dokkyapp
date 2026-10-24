-- последняя запись журнала агента до начала хода: по ней чат откатывается к сообщению
ALTER TABLE turns ADD COLUMN history_from INTEGER;
-- человек сам выбирал доступ с телефона; пока не выбирал, он включается после входа
ALTER TABLE remote_control ADD COLUMN configured INTEGER NOT NULL DEFAULT 0;
