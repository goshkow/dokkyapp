import asyncio

import pytest
from fastapi.testclient import TestClient

import koddi.storage.db as storage
from koddi.agents.runs import AgentRunRegistry
from koddi.api import create_app


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DB_PATH", tmp_path / "owners.sqlite3")
    storage.init_db()
    storage.set_setting("dokky_user", {"id": "a"})
    with storage.db() as conn:
        return conn.execute("INSERT INTO chats(title,owner_id) VALUES ('A','a')").lastrowid


def test_local_chat_routes_reject_old_account(isolated):
    client = TestClient(create_app())
    assert client.get(f"/api/chats/{isolated}/messages").status_code == 200
    storage.set_setting("dokky_user", {"id": "b"})
    for suffix in ("messages", "turn/events", "turn", "plan", "export", "changes"):
        assert client.get(f"/api/chats/{isolated}/{suffix}").status_code == 404
    assert client.post(f"/api/chats/{isolated}/turn/cancel").status_code == 404
    assert client.get("/api/chats").json() == []


@pytest.mark.asyncio
async def test_revoke_removes_replay_and_closes_existing_subscription(isolated):
    registry = AgentRunRegistry()
    gate = asyncio.Event()

    async def stream():
        yield 'event: message.delta\ndata: {"content":"private"}\n\n'
        await gate.wait()
        yield 'event: turn.completed\ndata: {}\n\n'

    run = await registry.start(isolated, stream(), owner_id="a")
    await asyncio.sleep(0)
    assert registry.get(isolated, owner_id="b") is None
    subscriber = run.subscribe()
    assert "private" in await anext(subscriber)
    await registry.cancel(isolated)
    registry.forget_completed()
    assert registry.get(isolated) is None
    assert not run.events and run.live_message is None
    assert [event async for event in subscriber] == []
    assert [event async for event in run.subscribe()] == []


def test_replay_is_bounded_and_expired(isolated):
    from koddi.agents.runs import ActiveRun
    from time import monotonic

    registry = AgentRunRegistry()
    run = ActiveRun(chat_id=isolated, completed=True, finished_at=monotonic() - 181)
    registry._runs[isolated] = run
    assert registry.get(isolated) is None


def test_used_history_cannot_silently_diverge(isolated):
    client = TestClient(create_app())
    with storage.db() as conn:
        message = conn.execute("INSERT INTO messages(chat_id,role,content) VALUES (?,'user','original')", (isolated,)).lastrowid
        conn.execute("INSERT INTO agent_history(chat_id,payload_json) VALUES (?,?)", (isolated, '{"role":"user","content":"original"}'))
    assert client.put(f"/api/chats/{isolated}/messages/{message}", json={"content":"changed"}).status_code == 409
    assert client.delete(f"/api/chats/{isolated}/messages/{message}").status_code == 409


def test_signin_cannot_take_another_accounts_chats_attachments_or_memory(isolated, tmp_path):
    from koddi.dokky.coding import adopt_local_data
    from koddi.agents.memory import retrieve_memory
    from koddi.tools import ToolRunner

    storage.set_setting('permission_profile', 'trusted')
    import io
    from koddi.attachments import store_attachment

    attachment = store_attachment(isolated, 'private.txt', io.BytesIO(b'private data A'))
    runner = ToolRunner(tmp_path)
    assert runner.run('memory.set', {'content': 'privateword owner A', 'kind': 'note'}, approved=True)['ok']
    assert retrieve_memory(None, 'privateword')[0]['owner_id'] == 'a'
    storage.set_setting('dokky_user', {'id': 'b'})
    adopt_local_data('b')
    client = TestClient(create_app())
    assert client.get(f'/api/chats/{isolated}/messages').status_code == 404
    assert retrieve_memory(None, 'privateword') == []
    assert client.get(f"/api/attachments/{attachment['id']}/content").status_code == 404
    with storage.db() as conn:
        assert conn.execute('SELECT owner_id FROM chats WHERE id=?', (isolated,)).fetchone()[0] == 'a'
        assert conn.execute('SELECT owner_id FROM memory_summaries').fetchone()[0] == 'a'
    storage.set_setting('dokky_user', {'id': 'a'})
    assert retrieve_memory(None, 'privateword')[0]['content'] == 'privateword owner A'


def test_logs_changes_and_rollback_do_not_cross_accounts_or_project(isolated, tmp_path):
    from koddi.logs.service import log_event, list_logs, search_logs
    from koddi.changes.service import latest_change_set, rollback_change_set
    from koddi.tools import ToolRunner

    storage.set_setting('permission_profile', 'trusted')
    first = tmp_path / 'first'
    second = tmp_path / 'second'
    first.mkdir()
    second.mkdir()
    with storage.db() as conn:
        project_a = conn.execute("INSERT INTO projects(name,path) VALUES ('first',?)", (str(first),)).lastrowid
        project_b = conn.execute("INSERT INTO projects(name,path) VALUES ('second',?)", (str(second),)).lastrowid
    runner_a = ToolRunner(first, project_a)
    result = runner_a.run('file.create', {'path': 'private.txt', 'content': 'owner A'}, approved=True)
    assert result['ok']
    log_event('INFO', 'private.a', 'private data A')
    assert any(item['event'] == 'private.a' for item in list_logs())
    assert ToolRunner(second, project_b).run('changes.latest', {})['change_set'] is None
    storage.set_setting('dokky_user', {'id': 'b'})
    assert latest_change_set() is None
    assert not any(item['event'] == 'private.a' for item in list_logs())
    assert search_logs('private data A') == []
    assert not rollback_change_set(result['change_set_id'])['rolled_back']
    assert not ToolRunner(first, project_a).run('file.restore', {'change_set_id': result['change_set_id']}, approved=True)['ok']
    assert (first / 'private.txt').read_text() == 'owner A'


def test_devservers_do_not_cross_account_or_workspace(isolated, tmp_path, monkeypatch):
    import koddi.tools.runner as module
    from koddi.tools import ToolRunner

    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    other = tmp_path / 'other'
    other.mkdir()
    stopped = []

    class Process:
        def poll(self):
            return None

    one, two, three = Process(), Process(), Process()
    entries = {
        10001: {'proc': one, 'command': 'one', 'workspace': str(workspace), 'owner_id': 'a', 'log': tmp_path / 'missing'},
        10002: {'proc': two, 'command': 'two', 'workspace': str(other), 'owner_id': 'a', 'log': tmp_path / 'missing'},
        10003: {'proc': three, 'command': 'three', 'workspace': str(workspace), 'owner_id': 'b', 'log': tmp_path / 'missing'},
    }
    monkeypatch.setattr(module, '_DEV_SERVERS', entries)
    monkeypatch.setattr(module, '_stop_process_tree', lambda process: stopped.append(process))
    runner = ToolRunner(workspace)
    assert [item['command'] for item in runner.detect_devserver()['running']] == ['one']
    assert not runner.devserver_stop({'pid': 10003})['ok']
    assert runner.devserver_stop({})['ok']
    assert stopped == [one] and set(entries) == {10002, 10003}
