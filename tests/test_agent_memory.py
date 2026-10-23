import koddi.storage.db as storage
from koddi.agents.memory import retrieve_memory


def test_memory_retrieval_is_scoped_ranked_and_bounded(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DB_PATH", tmp_path / "memory.sqlite3")
    storage.init_db()
    with storage.db() as conn:
        conn.executemany(
            "INSERT INTO memory_summaries(project_id,kind,content) VALUES (?,'note',?)",
            [
                (1, "Проект использует приветствие на Python"),
                (2, "Приватное приветствие другого проекта"),
                (None, "Глобальная заметка пользователя"),
                (1, "Новая нерелевантная заметка"),
            ],
        )
    found = retrieve_memory(1, "приветствие")
    assert len(found) == 1 and found[0]["project_id"] == 1
    assert all(m["project_id"] in {1, None} for m in retrieve_memory(1))
    assert all(m["project_id"] is None for m in retrieve_memory(None))
    with storage.db() as conn:
        conn.execute(
            "INSERT INTO memory_summaries(project_id,kind,content) VALUES (1,'note',?)",
            ("x" * 50000,),
        )
    assert sum(len(m["content"]) for m in retrieve_memory(1)) <= 12000
    assert max(len(m["content"]) for m in retrieve_memory(1)) <= 3000


def test_memory_index_tracks_updates_and_deletes(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DB_PATH", tmp_path / "index.sqlite3")
    storage.init_db()
    with storage.db() as conn:
        ident = conn.execute(
            "INSERT INTO memory_summaries(project_id,kind,content) VALUES (1,'note','oldword')"
        ).lastrowid
        conn.execute("UPDATE memory_summaries SET content='newword' WHERE id=?", (ident,))
    assert retrieve_memory(1, "newword")[0]["content"] == "newword"
    with storage.db() as conn:
        conn.execute("DELETE FROM memory_summaries WHERE id=?", (ident,))
    assert retrieve_memory(1, "newword") == []
