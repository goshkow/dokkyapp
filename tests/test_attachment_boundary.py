from __future__ import annotations

import io
import sqlite3
import zipfile
from contextlib import contextmanager
from pathlib import Path

import pytest
from fastapi import HTTPException

import koddi.attachments.service as attachments
import koddi.documents.service as documents
import koddi.storage.db as storage


@pytest.fixture(autouse=True)
def profile(tmp_path, monkeypatch):
    monkeypatch.setenv("KODDI_DATA_DIR", str(tmp_path / "profile"))
    monkeypatch.setenv("KODDI_INTERNAL_TEST", "0")
    monkeypatch.setattr(storage, "DB_PATH", tmp_path / "test.sqlite3")
    storage.init_db()
    storage.set_setting("dokky_user", {"id": "alice"})
    from koddi.dokky.client import dokky

    monkeypatch.setattr(dokky.store,'load',lambda:{'access_token':'fixture-session'})
    return tmp_path


def chat(owner="alice"):
    with storage.db() as conn:
        return conn.execute("INSERT INTO chats(owner_id) VALUES (?)", (owner,)).lastrowid


def upload(chat_id, name="note.txt", content=b"hello"):
    return attachments.store_attachment(chat_id, name, io.BytesIO(content))


def archive(entries, *, compression=zipfile.ZIP_STORED):
    result = io.BytesIO()
    with zipfile.ZipFile(result, "w", compression=compression) as output:
        for name, data in entries:
            output.writestr(name, data)
    return result.getvalue()


def docx_xml(text="hello"):
    return (
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        '<w:body><w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr>'
        f'<w:r><w:t>{text}</w:t></w:r></w:p></w:body></w:document>'
    ).encode()


def test_store_records_owner_and_rejects_foreign_or_missing_chat(profile):
    own = chat()
    other = chat("bob")
    item = upload(own)
    assert item["owner_id"] == "alice"
    assert item["chat_id"] == own
    for identifier in [other, 999999, 0]:
        with pytest.raises(HTTPException) as error:
            upload(identifier)
        assert error.value.status_code == 404
    assert len(attachments.list_attachments()) == 1


def test_list_and_delete_are_owner_scoped(profile):
    own_chat = chat()
    alice = upload(own_chat)
    storage.set_setting("dokky_user", {"id": "bob"})
    bob_chat = chat("bob")
    bob = upload(bob_chat, content=b"bob")
    assert [item["id"] for item in attachments.list_attachments()] == [bob["id"]]
    assert attachments.delete_attachment(alice["id"]) == {
        "ok": False, "error": "attachment not found",
    }
    assert Path(alice["stored_path"]).is_file()
    with pytest.raises(HTTPException):
        attachments.list_attachments(own_chat)
    assert attachments.delete_attachment(bob["id"])["ok"]
    assert not Path(bob["stored_path"]).exists()


def test_cross_owner_deduplication_does_not_delete_another_owners_blob(profile):
    first = upload(chat())
    storage.set_setting("dokky_user", {"id": "bob"})
    second = upload(chat("bob"))
    assert first["stored_path"] == second["stored_path"]
    assert attachments.delete_attachment(second["id"])["ok"]
    assert Path(first["stored_path"]).is_file()
    storage.set_setting("dokky_user", {"id": "alice"})
    assert attachments.read_attachment(first["id"], first["chat_id"])["text"] == "hello"
    assert attachments.delete_attachment(first["id"])["ok"]
    assert not Path(first["stored_path"]).exists()


def test_read_requires_exact_current_chat_before_reading_file(profile, monkeypatch):
    own_chat, another = chat(), chat()
    item = upload(own_chat)
    unattached = upload(None, content=b"unattached")

    def forbidden(*args):
        raise AssertionError("unauthorized file read")

    monkeypatch.setattr(attachments, "_read_attachment_bytes", forbidden)
    for identifier, chat_id in [(item["id"], another), (unattached["id"], own_chat), (99999, own_chat)]:
        with pytest.raises(HTTPException) as error:
            attachments.read_attachment(identifier, chat_id)
        assert error.value.status_code == 404
    storage.set_setting("dokky_user", {"id": "bob"})
    with pytest.raises(HTTPException):
        attachments.read_attachment(item["id"], own_chat)


def test_read_result_contains_only_bounded_text_not_storage_details(profile):
    own_chat = chat()
    item = upload(own_chat, content=b"x" * 120_010)
    result = attachments.read_attachment(item["id"], own_chat)
    assert result["text"] == "x" * 120_000
    assert result["truncated"]
    assert result["attachment_id"] == item["id"]
    assert not {"stored_path", "sha256", "source_bytes", "content"} & result.keys()
    assert not any(isinstance(value, bytes) for value in result.values())


@pytest.mark.parametrize("name,content", [
    ("image.png", b"\x89PNG"), ("archive.zip", b"PK"), ("file.pdf", b"%PDF-1.7"),
    ("fake.txt", b"\x00\x01binary"), ("invalid.md", b"\xff\xfe"),
])
def test_binary_or_unsupported_content_is_not_returned(profile, name, content):
    own_chat = chat()
    item = upload(own_chat, name, content)
    with pytest.raises(ValueError):
        attachments.read_attachment(item["id"], own_chat)


def test_missing_deleted_tampered_and_oversized_blobs_fail_closed(profile, monkeypatch):
    own_chat = chat()
    deleted = upload(own_chat, content=b"deleted")
    assert attachments.delete_attachment(deleted["id"])["ok"]
    with pytest.raises(HTTPException):
        attachments.read_attachment(deleted["id"], own_chat)
    changed = upload(own_chat, content=b"original")
    Path(changed["stored_path"]).write_bytes(b"modified")
    with pytest.raises(ValueError, match="changed"):
        attachments.read_attachment(changed["id"], own_chat)
    missing = upload(own_chat, content=b"missing")
    Path(missing["stored_path"]).unlink()
    with pytest.raises(ValueError, match="unavailable"):
        attachments.read_attachment(missing["id"], own_chat)
    oversized = upload(own_chat, content=b"123456")
    monkeypatch.setattr(documents, "MAX_DOCUMENT_BYTES", 5)
    with pytest.raises(ValueError, match="limit"):
        attachments.read_attachment(oversized["id"], own_chat)


def test_blob_path_cannot_point_outside_storage(profile):
    own_chat = chat()
    item = upload(own_chat)
    outside = profile / "private.txt"
    outside.write_bytes(b"private")
    with storage.db() as conn:
        conn.execute("UPDATE attachments SET stored_path=? WHERE id=?", (str(outside), item["id"]))
    with pytest.raises(ValueError, match="unavailable"):
        attachments.read_attachment(item["id"], own_chat)
    with pytest.raises(ValueError, match="unavailable"):
        attachments.delete_attachment(item["id"])
    assert outside.read_bytes() == b"private"


def test_linked_blob_is_rejected(profile):
    own_chat = chat()
    item = upload(own_chat)
    path = Path(item["stored_path"])
    path.unlink()
    target = profile / "other.txt"
    target.write_bytes(b"hello")
    try:
        path.symlink_to(target)
    except OSError:
        pytest.skip("symlinks require privileges on this Windows installation")
    with pytest.raises(ValueError, match="unavailable"):
        attachments.read_attachment(item["id"], own_chat)


@pytest.mark.parametrize("suffix,entries,expected", [
    (".docx", [("word/document.xml", docx_xml())], "hello"),
    (".pptx", [("ppt/slides/slide1.xml",
                b'<a:p xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"><a:t>slide</a:t></a:p>')], "slide"),
    (".xlsx", [("xl/worksheets/sheet1.xml",
                b'<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>'
                b'<row><c t="inlineStr"><is><t>cell</t></is></c></row></sheetData></worksheet>')], "cell"),
])
def test_office_documents_extract_bounded_text(profile, suffix, entries, expected):
    own_chat = chat()
    item = upload(own_chat, "document" + suffix, archive(entries))
    assert attachments.read_attachment(item["id"], own_chat)["text"] == expected


@pytest.mark.parametrize("entries", [
    [("word/document.xml", b'<!DOCTYPE r [<!ENTITY x "entity">]><r>&x;</r>')],
    [("word/document.xml", b"<a>" * 65 + b"text" + b"</a>" * 65)],
    [("word/document.xml", b"<broken>")],
    [("word/document.xml", b"not XML")],
    [("../word/document.xml", docx_xml())],
    [("word/document.xml", docx_xml()), ("word/document.xml", docx_xml())],
])
def test_unsafe_office_archives_are_rejected(profile, entries):
    own_chat = chat()
    item = upload(own_chat, "document.docx", archive(entries))
    with pytest.raises(ValueError):
        attachments.read_attachment(item["id"], own_chat)


def test_zip_bomb_and_archive_member_limits(profile, monkeypatch):
    data = archive([("word/document.xml", b"x" * 100_000)], compression=zipfile.ZIP_DEFLATED)
    with pytest.raises(ValueError, match="Unsafe document"):
        documents.extract_text(Path("bomb.docx"), source_bytes=data)
    monkeypatch.setattr(documents, "MAX_ARCHIVE_MEMBERS", 2)
    data = archive([("word/document.xml", docx_xml()), ("one", b"a"), ("two", b"b")])
    with pytest.raises(ValueError, match="too many"):
        documents.extract_text(Path("many.docx"), source_bytes=data)
    monkeypatch.setattr(documents, "MAX_ARCHIVE_MEMBERS", 512)
    monkeypatch.setattr(documents, "MAX_XML_BYTES", 20)
    with pytest.raises(ValueError, match="XML exceeds"):
        documents.extract_text(Path("large.docx"), source_bytes=archive([("word/document.xml", docx_xml())]))


def test_repeated_shared_strings_cannot_expand_output_without_bound(profile):
    namespace = b' xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
    shared = b"<sst" + namespace + b"><si><t>" + b"x" * 10_000 + b"</t></si></sst>"
    sheet = b"<worksheet" + namespace + b"><sheetData><row>" + b'<c t="s"><v>0</v></c>' * 1000
    sheet += b"</row></sheetData></worksheet>"
    data = archive([("xl/sharedStrings.xml", shared), ("xl/worksheets/sheet1.xml", sheet)])
    text, _ = documents.extract_text(Path("sheet.xlsx"), 100, source_bytes=data)
    assert len(text) == 100


def test_document_index_and_listing_inherit_attachment_owner(profile):
    own_chat = chat()
    item = upload(own_chat)
    document = documents.index_document(item["stored_path"], item["id"])
    assert document["owner_id"] == "alice"
    assert [row["id"] for row in documents.list_documents()] == [document["id"]]
    storage.set_setting("dokky_user", {"id": "bob"})
    assert documents.list_documents() == []
    with pytest.raises(HTTPException):
        documents.index_document(item["stored_path"], item["id"])
    storage.set_setting("dokky_user", {"id": "alice"})
    assert attachments.delete_attachment(item["id"])["ok"]
    assert documents.list_documents() == []


def test_unowned_or_unlinked_documents_and_arbitrary_source_paths_are_denied(profile):
    own_chat = chat()
    item = upload(own_chat)
    with pytest.raises(HTTPException) as error:
        documents.index_document(item["stored_path"])
    assert error.value.status_code == 422
    with pytest.raises(HTTPException):
        documents.index_document(str(profile / "secret.txt"), item["id"])
    with storage.db() as conn:
        conn.execute("INSERT INTO documents(source_path, kind, summary) VALUES ('hidden', 'txt', 'secret')")
        conn.execute("UPDATE attachments SET owner_id='' WHERE id=?", (item["id"],))
    assert attachments.list_attachments() == []
    assert documents.list_documents() == []
    with pytest.raises(HTTPException):
        attachments.read_attachment(item["id"], own_chat)


@pytest.mark.parametrize("operation", ["read", "index"])
def test_account_switch_during_extraction_does_not_return_or_index_old_content(profile, monkeypatch, operation):
    own_chat = chat()
    item = upload(own_chat)
    extract = documents.extract_text

    def switch(*args, **kwargs):
        result = extract(*args, **kwargs)
        storage.set_setting("dokky_user", {"id": "bob"})
        return result

    monkeypatch.setattr(documents, "extract_text", switch)
    with pytest.raises(HTTPException):
        if operation == "read":
            attachments.read_attachment(item["id"], own_chat)
        else:
            documents.index_document(item["stored_path"], item["id"])
    with storage.db() as conn:
        assert conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 0


def test_account_switch_during_upload_cleans_temporary_file(profile):
    own_chat = chat()

    class SwitchingStream(io.BytesIO):
        def read(self, size=-1):
            storage.set_setting("dokky_user", {"id": "bob"})
            return super().read(size)

    with pytest.raises(HTTPException):
        attachments.store_attachment(own_chat, "note.txt", SwitchingStream(b"secret"))
    assert not list((profile / "profile" / "attachments").glob("upload-*"))
    with storage.db() as conn:
        assert conn.execute("SELECT COUNT(*) FROM attachments").fetchone()[0] == 0


@pytest.mark.parametrize("operation", [
    lambda: attachments.list_attachments(),
    lambda: attachments.delete_attachment(1),
    lambda: attachments.require_attachment(1),
    lambda: attachments.read_attachment(1, 1),
    lambda: upload(None),
    lambda: documents.index_document("anything"),
    lambda: documents.list_documents(),
])
def test_signed_out_calls_deny_by_default(profile, operation):
    storage.set_setting("dokky_user", {})
    with pytest.raises(HTTPException) as error:
        operation()
    assert error.value.status_code == 401


def test_upload_database_failures_do_not_leave_files(profile, monkeypatch):
    with storage.db() as conn:
        conn.execute("""CREATE TRIGGER reject_attachment BEFORE INSERT ON attachments
                        BEGIN SELECT RAISE(ABORT, 'rejected'); END""")
    with pytest.raises(sqlite3.IntegrityError):
        upload(None)
    directory = profile / "profile" / "attachments"
    assert list(directory.iterdir()) == []

    @contextmanager
    def unavailable():
        raise sqlite3.OperationalError("unavailable")
        yield

    monkeypatch.setattr(attachments, "db", unavailable)
    with pytest.raises(sqlite3.OperationalError):
        upload(None)
    assert list(directory.iterdir()) == []


def test_inconsistent_chat_owner_is_not_exposed(profile):
    own_chat = chat()
    item = upload(own_chat)
    documents.index_document(item["stored_path"], item["id"])
    with storage.db() as conn:
        conn.execute("UPDATE chats SET owner_id='bob' WHERE id=?", (own_chat,))
    assert attachments.list_attachments() == []
    assert documents.list_documents() == []
    with pytest.raises(HTTPException):
        attachments.delete_attachment(item["id"])
    assert Path(item["stored_path"]).is_file()


def test_delete_during_extraction_prevents_stale_read(profile, monkeypatch):
    own_chat = chat()
    item = upload(own_chat)
    extract = documents.extract_text

    def delete(*args, **kwargs):
        result = extract(*args, **kwargs)
        attachments.delete_attachment(item["id"])
        return result

    monkeypatch.setattr(documents, "extract_text", delete)
    with pytest.raises(HTTPException):
        attachments.read_attachment(item["id"], own_chat)
