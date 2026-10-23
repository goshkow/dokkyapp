CREATE VIRTUAL TABLE IF NOT EXISTS memory_search USING fts5(
  content, content='memory_summaries', content_rowid='id', tokenize='unicode61'
);
CREATE TRIGGER IF NOT EXISTS memory_search_insert AFTER INSERT ON memory_summaries BEGIN
  INSERT INTO memory_search(rowid,content) VALUES (new.id,new.content);
END;
CREATE TRIGGER IF NOT EXISTS memory_search_delete AFTER DELETE ON memory_summaries BEGIN
  INSERT INTO memory_search(memory_search,rowid,content) VALUES ('delete',old.id,old.content);
END;
CREATE TRIGGER IF NOT EXISTS memory_search_update AFTER UPDATE OF content ON memory_summaries BEGIN
  INSERT INTO memory_search(memory_search,rowid,content) VALUES ('delete',old.id,old.content);
  INSERT INTO memory_search(rowid,content) VALUES (new.id,new.content);
END;
INSERT INTO memory_search(memory_search) VALUES ('rebuild');
