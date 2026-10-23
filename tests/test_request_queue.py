import asyncio
import uuid

import pytest
from fastapi import HTTPException

import koddi.storage.db as storage
from koddi.agents.queue import RequestQueue


@pytest.fixture
def queue_chat(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DB_PATH", tmp_path / "queue.sqlite3")
    storage.init_db()
    storage.set_setting("dokky_user", {"id": "owner"})
    with storage.db() as conn:
        chat = conn.execute("INSERT INTO chats(title,owner_id) VALUES ('queue','owner')").lastrowid
    return RequestQueue(), chat


def payload(text):
    return {
        "content": text,
        "mode": "development",
        "model": None,
        "options": {},
        "attachment_ids": [],
    }


@pytest.mark.asyncio
async def test_fifo_and_idempotent_requests(queue_chat):
    queue, chat = queue_chat
    seen = []

    async def launch(chat_id, body, key):
        seen.append(body["content"])
        await asyncio.sleep(0)
        return "completed"

    queue.pause(chat)
    queue.launch = launch
    key = str(uuid.uuid4())
    queue.add(chat, payload("first"), key)
    queue.add(chat, payload("first"), key)
    queue.add(chat, payload("second"), str(uuid.uuid4()))
    assert len(queue.snapshot(chat)["items"]) == 2
    queue.resume(chat)
    await queue.workers[chat]
    assert seen == ["first", "second"]
    queue.add(chat, payload("first"), key)
    await queue.workers[chat]
    assert seen == ["first", "second"]
    with pytest.raises(HTTPException) as error:
        queue.add(chat, payload("different"), key)
    assert error.value.status_code == 409


@pytest.mark.asyncio
async def test_failure_pauses_and_restart_never_replays_running(queue_chat):
    queue, chat = queue_chat
    seen = []

    async def launch(chat_id, body, key):
        seen.append(key)
        return "failed"

    queue.pause(chat)
    queue.launch = launch
    first = str(uuid.uuid4())
    queue.add(chat, payload("one"), first)
    queue.add(chat, payload("two"), str(uuid.uuid4()))
    queue.resume(chat)
    await queue.workers[chat]
    assert seen == [first]
    assert queue.snapshot(chat)["paused"]
    with storage.db() as conn:
        conn.execute("UPDATE queued_requests SET status='running' WHERE id=?", (first,))
    queue.recover()
    assert queue.snapshot(chat)["items"][0]["status"] == "failed"
    assert queue.snapshot(chat)["paused"]


def test_edit_version_and_cross_account_isolation(queue_chat):
    queue, chat = queue_chat
    key = str(uuid.uuid4())
    queue.add(chat, payload("one"), key)
    queue.edit(chat, key, 1, "changed")
    with pytest.raises(HTTPException) as conflict:
        queue.edit(chat, key, 1, None)
    assert conflict.value.status_code == 409
    storage.set_setting("dokky_user", {"id": "other"})
    for action in (
        lambda: queue.snapshot(chat),
        lambda: queue.edit(chat, key, 2, None),
        lambda: queue.resume(chat),
    ):
        with pytest.raises(HTTPException) as denied:
            action()
        assert denied.value.status_code == 404


@pytest.mark.asyncio
async def test_stop_keeps_pending_and_project_change_pauses(queue_chat):
    queue, chat = queue_chat
    busy = True
    queue.active = lambda _: busy
    queue.last_status = lambda _: "cancelled"
    queue.launch = lambda *_: pytest.fail("must not launch")
    queue.add(chat, payload("pending"), str(uuid.uuid4()))
    await asyncio.sleep(0)
    queue.pause(chat)
    busy = False
    await queue.workers[chat]
    assert len(queue.snapshot(chat)["items"]) == 1
    with storage.db() as conn:
        project = conn.execute("INSERT INTO projects(name,path) VALUES ('p','p')").lastrowid
        conn.execute("UPDATE chats SET project_id=? WHERE id=?", (project, chat))
    queue.resume(chat)
    await queue.workers[chat]
    assert queue.snapshot(chat)["paused"]


def test_promote_puts_request_first_and_rejects_stale_version(queue_chat):
    queue, chat = queue_chat
    queue.pause(chat)
    keys = [str(uuid.uuid4()) for _ in range(3)]
    for index, key in enumerate(keys):
        queue.add(chat, payload(f"task {index}"), key)
    last = queue.snapshot(chat)["items"][2]
    snapshot = queue.promote(chat, keys[2], last["version"])
    assert [item["content"] for item in snapshot["items"]] == ["task 2", "task 0", "task 1"]
    with pytest.raises(HTTPException) as stale:
        queue.promote(chat, keys[2], last["version"])
    assert stale.value.status_code == 409
    with pytest.raises(HTTPException) as missing:
        queue.promote(chat, str(uuid.uuid4()), 1)
    assert missing.value.status_code == 404


@pytest.mark.asyncio
async def test_interrupt_runs_promoted_request_after_user_stop(queue_chat):
    queue, chat = queue_chat
    busy = True
    seen = []
    queue.active = lambda _: busy
    queue.last_status = lambda _: "cancelled"

    async def launch(chat_id, body, key):
        seen.append(body["content"])
        return "completed"

    queue.launch = launch
    keys = [str(uuid.uuid4()), str(uuid.uuid4())]
    queue.add(chat, payload("waits"), keys[0])
    queue.add(chat, payload("urgent"), keys[1])
    await asyncio.sleep(0)
    item = queue.snapshot(chat)["items"][1]
    queue.promote(chat, keys[1], item["version"])
    # пользователь остановил текущую задачу: обычно очередь встаёт на паузу, но запрос из очереди запустили намеренно
    busy = False
    await queue.workers[chat]
    assert seen[0] == "urgent"
