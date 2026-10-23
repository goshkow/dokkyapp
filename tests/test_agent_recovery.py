import json

import koddi.storage.db as storage
from koddi.agents.recovery import recover_interrupted_runs


def test_recovery_preserves_work_owned_by_other_live_process(tmp_path, monkeypatch):
    from koddi.agents.recovery import current_owner

    monkeypatch.setattr(storage, "DB_PATH", tmp_path / "owner.sqlite3")
    storage.init_db()
    with storage.db() as conn:
        chat = conn.execute("INSERT INTO chats(title) VALUES ('Live')").lastrowid
        conn.execute(
            "INSERT INTO turns(chat_id,status,owner_pid,owner_token) VALUES (?,'running',?,?)",
            (chat, *current_owner()),
        )
    assert recover_interrupted_runs(lambda _: False) == []


def test_restart_recovers_abandoned_state_without_replaying_tools(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DB_PATH", tmp_path / "recovery.sqlite3")
    storage.init_db()
    with storage.db() as conn:
        chat = conn.execute("INSERT INTO chats(title) VALUES ('Interrupted')").lastrowid
        turn = conn.execute(
            "INSERT INTO turns(chat_id,status) VALUES (?,'running')", (chat,)
        ).lastrowid
        message = conn.execute(
            "INSERT INTO messages(chat_id,role,content,metadata_json) VALUES (?,'assistant','Уже написанный текст',?)",
            (chat, json.dumps({"status": "running", "events": []})),
        ).lastrowid
        conn.execute(
            "INSERT INTO agent_history(chat_id,payload_json) VALUES (?,?)",
            (chat, json.dumps({"role": "assistant", "content": "Context"})),
        )
    assert recover_interrupted_runs(lambda _: True) == []
    assert recover_interrupted_runs(lambda _: False) == [turn]
    assert recover_interrupted_runs(lambda _: False) == []
    with storage.db() as conn:
        assert (
            conn.execute("SELECT status FROM turns WHERE id=?", (turn,)).fetchone()[0] == "failed"
        )
        row = conn.execute(
            "SELECT content,metadata_json FROM messages WHERE id=?", (message,)
        ).fetchone()
        assert row[0] == "Уже написанный текст"
        assert json.loads(row[1])["interrupted"] is True
        assert conn.execute("SELECT COUNT(*) FROM agent_history").fetchone()[0] == 1
