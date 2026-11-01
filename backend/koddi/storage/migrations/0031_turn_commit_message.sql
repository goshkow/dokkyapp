-- сообщение коммита, которое агент предложил при завершении хода
ALTER TABLE turn_quality ADD COLUMN commit_message TEXT;
