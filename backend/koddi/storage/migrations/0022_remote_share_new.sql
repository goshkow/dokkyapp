-- новые чаты окна сразу видны на сайте и телефоне; выключено - чат появляется там, только если включить его вручную
ALTER TABLE remote_control ADD COLUMN share_new INTEGER NOT NULL DEFAULT 1;
