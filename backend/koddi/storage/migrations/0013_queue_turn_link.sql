ALTER TABLE turns ADD COLUMN queued_request_id TEXT;
CREATE UNIQUE INDEX turns_queue_request ON turns(queued_request_id) WHERE queued_request_id IS NOT NULL;
