CREATE TABLE file_operations (
 id TEXT PRIMARY KEY,
 owner_id TEXT NOT NULL CHECK(length(owner_id) > 0),
 project_id INTEGER,
 turn_id INTEGER,
 root TEXT NOT NULL,
 root_dev TEXT NOT NULL,
 root_ino TEXT NOT NULL,
 recovery_of TEXT REFERENCES file_operations(id),
 status TEXT NOT NULL DEFAULT 'prepared'
   CHECK(status IN ('prepared', 'recovering', 'finished', 'restored')),
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 completed_at TEXT
);
CREATE INDEX file_operations_pending ON file_operations(owner_id, project_id, status);
CREATE UNIQUE INDEX file_operations_recovery ON file_operations(recovery_of)
 WHERE recovery_of IS NOT NULL AND status IN ('prepared', 'recovering');

CREATE TABLE file_operation_entries (
 operation_id TEXT NOT NULL REFERENCES file_operations(id) ON DELETE CASCADE,
 path TEXT NOT NULL,
 path_key TEXT NOT NULL,
 before_hash TEXT,
 after_hash TEXT,
 before_content BLOB,
 after_content BLOB,
 PRIMARY KEY(operation_id, path_key),
 CHECK((before_content IS NULL AND before_hash IS NULL) OR
       (typeof(before_content) = 'blob' AND before_hash IS NOT NULL AND length(before_hash) = 64)),
 CHECK((after_content IS NULL AND after_hash IS NULL) OR
       (typeof(after_content) = 'blob' AND after_hash IS NOT NULL AND length(after_hash) = 64))
);
CREATE INDEX file_operation_paths ON file_operation_entries(path_key, operation_id);
