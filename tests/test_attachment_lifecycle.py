import io
from concurrent.futures import ThreadPoolExecutor

import koddi.storage.db as storage
from koddi.attachments import delete_attachment, list_attachments, store_attachment


def test_same_filename_parallel_uploads_do_not_mix_contents(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DB_PATH", tmp_path / "attachments.sqlite3")
    storage.init_db()
    values = [b"a" * 100000, b"b" * 100000]
    with ThreadPoolExecutor(max_workers=2) as pool:
        items = list(
            pool.map(lambda value: store_attachment(None, "same.txt", io.BytesIO(value)), values)
        )
    from pathlib import Path

    assert [Path(item["stored_path"]).read_bytes() for item in items] == values


def test_delete_preserves_shared_blob_until_last_reference(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DB_PATH", tmp_path / "shared.sqlite3")
    storage.init_db()
    first = store_attachment(None, "note.txt", io.BytesIO(b"shared content"))
    second = store_attachment(None, "same.txt", io.BytesIO(b"shared content"))
    from pathlib import Path

    path = Path(first["stored_path"])
    assert delete_attachment(first["id"])["ok"] and path.is_file()
    assert [item["id"] for item in list_attachments()] == [second["id"]]
    assert delete_attachment(second["id"])["ok"] and not path.exists()
