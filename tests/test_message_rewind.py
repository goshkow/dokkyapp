import json

from fastapi.testclient import TestClient

import koddi.storage.db as storage


def _seed(conn, chat: int) -> dict:
    def message(role: str, content: str, metadata: dict | None = None) -> int:
        return conn.execute(
            "INSERT INTO messages(chat_id,role,content,metadata_json) VALUES (?,?,?,?)",
            (chat, role, content, json.dumps(metadata or {})),
        ).lastrowid

    def history(payload: dict) -> int:
        return conn.execute("INSERT INTO agent_history(chat_id,payload_json) VALUES (?,?)", (chat, json.dumps(payload))).lastrowid

    first_turn = conn.execute("INSERT INTO turns(chat_id,status,history_from) VALUES (?,'completed',0)", (chat,)).lastrowid
    message("user", "первый", {"turn_id": first_turn})
    history({"role": "user", "content": "первый"})
    kept = history({"role": "assistant", "content": "ответ 1"})
    message("assistant", "ответ 1", {"turn_id": first_turn})
    second_turn = conn.execute("INSERT INTO turns(chat_id,status,history_from) VALUES (?,'completed',?)", (chat, kept)).lastrowid
    attachment = conn.execute(
        "INSERT INTO attachments(chat_id,original_name,stored_path,media_type,size_bytes,sha256) VALUES (?,?,?,?,?,?)",
        (chat, "план.txt", "/tmp/x", "text/plain", 5, "0" * 64),
    ).lastrowid
    target = message("user", "второй", {"turn_id": second_turn, "attachment_ids": [attachment]})
    history({"role": "user", "content": "второй"})
    history({"role": "assistant", "content": "ответ 2"})
    message("assistant", "ответ 2", {"turn_id": second_turn})
    conn.execute("INSERT INTO agent_plans(turn_id,steps_json) VALUES (?, '[]')", (second_turn,))
    conn.execute(
        "INSERT INTO coding_sessions(chat_id,user_id,session_json,messages_json,history_json) VALUES (?,?,?,?,?)",
        (chat, "u", "{}", json.dumps([{"role": "user", "content": "второй"}]), json.dumps({"length": 1, "hash": "x"})),
    )
    return {"target": target, "second_turn": second_turn, "attachment": attachment, "kept": kept}


def test_rewind_removes_message_and_everything_after(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DB_PATH", tmp_path / "rewind.sqlite3")
    from koddi.api import create_app

    with TestClient(create_app()) as client:
        chat = client.post("/api/chats", json={"title": "Откат"}).json()["id"]
        with storage.db() as conn:
            seeded = _seed(conn, chat)
        base = f"/api/chats/{chat}/messages/{seeded['target']}/rewind"
        assert client.get(base).json() == {"messages_after": 1, "file_changes": 0}
        assert client.post(base, json={"files": "yes"}).status_code == 422
        result = client.post(base, json={}).json()
        assert result["content"] == "второй"
        assert [item["id"] for item in result["attachments"]] == [seeded["attachment"]]
        with storage.db() as conn:
            contents = [row[0] for row in conn.execute("SELECT content FROM messages WHERE chat_id=? ORDER BY id", (chat,))]
            history = [json.loads(row[0])["content"] for row in conn.execute("SELECT payload_json FROM agent_history WHERE chat_id=? ORDER BY id", (chat,))]
            turns = conn.execute("SELECT COUNT(*) FROM turns WHERE chat_id=?", (chat,)).fetchone()[0]
            plans = conn.execute("SELECT COUNT(*) FROM agent_plans WHERE turn_id=?", (seeded["second_turn"],)).fetchone()[0]
            coding = conn.execute("SELECT messages_json, history_json FROM coding_sessions WHERE chat_id=?", (chat,)).fetchone()
        assert contents == ["первый", "ответ 1"]
        assert history == ["первый", "ответ 1"]
        assert turns == 1 and plans == 0
        assert json.loads(coding[0]) == [] and json.loads(coding[1]) is None
        # ответ ассистента не правится, чужой чат не виден
        assert client.post(f"/api/chats/{chat}/messages/{seeded['target'] - 1}/rewind", json={}).status_code in {404, 422}
        assert client.post(f"/api/chats/999999/messages/{seeded['target']}/rewind", json={}).status_code == 404


def test_rewind_keeps_chat_when_files_fail_to_roll_back(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DB_PATH", tmp_path / "rewind-files.sqlite3")
    import koddi.changes as changes
    from koddi.agents import rewind as module
    from koddi.api import create_app

    monkeypatch.setattr(module, "_change_sets", lambda conn, chat_id, turn_id: [{"id": 1}, {"id": 2}])
    monkeypatch.setattr(changes, "rollback_change_set", lambda change_set: {"rolled_back": change_set["id"] == 2})
    with TestClient(create_app()) as client:
        chat = client.post("/api/chats", json={"title": "Файлы"}).json()["id"]
        with storage.db() as conn:
            seeded = _seed(conn, chat)
        response = client.post(f"/api/chats/{chat}/messages/{seeded['target']}/rewind", json={"files": True})
        assert response.status_code == 409 and "1 из 2" in response.json()["detail"]
        with storage.db() as conn:
            assert conn.execute("SELECT COUNT(*) FROM messages WHERE chat_id=?", (chat,)).fetchone()[0] == 4


def test_rewind_stops_when_a_turn_started_meanwhile(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DB_PATH", tmp_path / "rewind-race.sqlite3")
    from koddi.agents.recovery import current_owner
    from koddi.api import create_app

    with TestClient(create_app()) as client:
        chat = client.post("/api/chats", json={"title": "Гонка"}).json()["id"]
        with storage.db() as conn:
            seeded = _seed(conn, chat)
            # ход этого процесса уже в базе, а в реестре запусков ещё нет
            conn.execute(
                "INSERT INTO turns(chat_id,status,owner_pid,owner_token,history_from) VALUES (?,'running',?,?,0)",
                (chat, *current_owner()),
            )
        response = client.post(f"/api/chats/{chat}/messages/{seeded['target']}/rewind", json={})
        assert response.status_code == 409
        with storage.db() as conn:
            assert conn.execute("SELECT COUNT(*) FROM messages WHERE chat_id=?", (chat,)).fetchone()[0] == 4
