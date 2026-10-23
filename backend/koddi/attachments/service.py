from __future__ import annotations

import hashlib
import mimetypes
import os
import stat
import uuid
from pathlib import Path
from typing import BinaryIO

from fastapi import HTTPException

from koddi.policy.account import local_owner, owns, require_chat
from koddi.storage.db import db, runtime_path


MAX_ATTACHMENT_BYTES = 100 * 1024 * 1024
ALLOWED_EXTENSIONS = {
    ".png",
    ".jpg",
    ".jpeg",
    ".webp",
    ".gif",
    ".pdf",
    ".docx",
    ".xlsx",
    ".pptx",
    ".txt",
    ".md",
    ".zip",
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def store_attachment(chat_id: int | None, filename: str, stream: BinaryIO) -> dict:
    owner = local_owner()
    if chat_id is not None:
        chat = require_chat(chat_id)
        if not owns(chat["owner_id"], owner):
            raise HTTPException(404, "Чат не найден")
    if not isinstance(filename, str) or not filename or len(filename) > 255 or "\x00" in filename:
        raise ValueError("Invalid attachment name")
    filename = Path(filename.replace("\\", "/")).name
    suffix = Path(filename).suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        raise ValueError(f"Unsupported attachment type: {suffix}")
    target_dir = runtime_path("attachments", "placeholder").parent
    target_dir.mkdir(parents=True, exist_ok=True)
    if target_dir.is_symlink() or (
        hasattr(target_dir, "is_junction") and target_dir.is_junction()
    ):
        raise ValueError("Attachment storage is unavailable")
    temp_path = target_dir / f"upload-{uuid.uuid4().hex}{suffix}"
    size = 0
    try:
        with temp_path.open("xb") as out:
            while True:
                chunk = stream.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > MAX_ATTACHMENT_BYTES:
                    raise ValueError("Attachment exceeds size limit")
                out.write(chunk)
    except BaseException:
        temp_path.unlink(missing_ok=True)
        raise
    try:
        digest = _sha256(temp_path)
        final = target_dir / f"{digest}{suffix}"
        media_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        with db() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if local_owner() != owner:
                temp_path.unlink(missing_ok=True)
                raise HTTPException(404, "Вложение не найдено")
            if chat_id is not None:
                try:
                    require_chat(chat_id)
                except BaseException:
                    temp_path.unlink(missing_ok=True)
                    raise
            created_blob = not final.exists()
            if final.exists():
                if final.is_symlink() or not final.is_file():
                    temp_path.unlink(missing_ok=True)
                    raise ValueError("Attachment storage is unavailable")
                temp_path.unlink(missing_ok=True)
            else:
                temp_path.replace(final)
            try:
                cur = conn.execute(
                    """
                    INSERT INTO attachments(
                        chat_id, original_name, stored_path, media_type, size_bytes, sha256, owner_id
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (chat_id, filename, str(final), media_type, size, digest, owner),
                )
            except BaseException:
                if created_blob:
                    final.unlink(missing_ok=True)
                raise
            row = conn.execute("SELECT * FROM attachments WHERE id=?", (cur.lastrowid,)).fetchone()
        item = dict(row)
        item["metadata"] = {}
        return item
    finally:
        temp_path.unlink(missing_ok=True)


def list_attachments(chat_id: int | None = None) -> list[dict]:
    owner = local_owner()
    if chat_id is not None:
        require_chat(chat_id)
    legacy_owner = "" if owns("", owner) else owner
    with db() as conn:
        rows = conn.execute(
            """SELECT a.* FROM attachments a LEFT JOIN chats c ON c.id=a.chat_id
               WHERE a.status<>'deleted' AND a.owner_id IN (?, ?)
                 AND (a.chat_id IS NULL OR c.owner_id IN (?, ?))
                 AND (? IS NULL OR a.chat_id=?) ORDER BY a.id DESC LIMIT 200""",
            (owner, legacy_owner, owner, legacy_owner, chat_id, chat_id),
        ).fetchall()
    if local_owner() != owner:
        raise HTTPException(404, "Вложение не найдено")
    result = []
    for row in rows:
        item = dict(row)
        item["metadata"] = {}
        result.append(item)
    return result


def delete_attachment(attachment_id: int) -> dict:
    owner = local_owner()
    with db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT * FROM attachments WHERE id=?", (attachment_id,)).fetchone()
        if not row or row["status"] == "deleted" or not owns(row["owner_id"], owner):
            return {"ok": False, "error": "attachment not found"}
        if row["chat_id"] is not None:
            require_chat(row["chat_id"])
        if local_owner() != owner:
            raise HTTPException(404, "Вложение не найдено")
        path = _blob_path(dict(row))
        conn.execute("UPDATE attachments SET status='deleted' WHERE id=?", (attachment_id,))
        still_used = conn.execute(
            "SELECT 1 FROM attachments WHERE stored_path=? AND status<>'deleted' LIMIT 1",
            (str(path),),
        ).fetchone()
        if not still_used:
            path.unlink(missing_ok=True)
    return {"ok": True, "id": attachment_id}


def require_attachment(attachment_id: int) -> dict:
    owner = local_owner()
    with db() as conn:
        row = conn.execute(
            "SELECT * FROM attachments WHERE id=? AND status<>'deleted'", (attachment_id,),
        ).fetchone()
    if row is None or not owns(row["owner_id"], owner):
        raise HTTPException(404, "Вложение не найдено")
    if row["chat_id"] is not None:
        chat = require_chat(row["chat_id"])
        if not owns(chat["owner_id"], owner):
            raise HTTPException(404, "Вложение не найдено")
    if local_owner() != owner:
        raise HTTPException(404, "Вложение не найдено")
    return dict(row)


def _blob_path(item: dict) -> Path:
    root = runtime_path("attachments", "placeholder").parent
    path = Path(item["stored_path"])
    digest = item["sha256"]
    suffix = Path(item["original_name"]).suffix.lower()
    if (
        not isinstance(digest, str) or len(digest) != 64
        or any(char not in "0123456789abcdef" for char in digest)
        or suffix not in ALLOWED_EXTENSIONS or not path.is_absolute()
        or path.parent != root or path.name != f"{digest}{suffix}"
        or path.is_symlink() or root.is_symlink()
        or (hasattr(root, "is_junction") and root.is_junction())
    ):
        raise ValueError("Attachment storage is unavailable")
    return path


def _read_attachment_bytes(item: dict, max_bytes: int) -> bytes:
    if item["size_bytes"] < 0 or item["size_bytes"] > max_bytes:
        raise ValueError("Attachment exceeds document input limit")
    path = _blob_path(item)
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    flags |= getattr(os, "O_NONBLOCK", 0)
    try:
        with os.fdopen(os.open(path, flags), "rb") as handle:
            info = os.fstat(handle.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > max_bytes:
                raise ValueError("Attachment exceeds document input limit")
            data = handle.read(max_bytes + 1)
    except OSError as exc:
        raise ValueError("Attachment content is unavailable") from exc
    # проверяется именно прочитанный снимок, а не файл, который могли подменить после проверки
    if len(data) != item["size_bytes"] or hashlib.sha256(data).hexdigest() != item["sha256"]:
        raise ValueError("Attachment content changed")
    return data


def read_attachment(attachment_id: int, chat_id: int) -> dict:
    from koddi.documents.service import MAX_DOCUMENT_BYTES, extract_text

    owner = local_owner()
    require_chat(chat_id)
    item = require_attachment(attachment_id)
    if item["chat_id"] != chat_id or not owns(item["owner_id"], owner):
        raise HTTPException(404, "Вложение не найдено")
    if Path(item["original_name"]).suffix.lower() not in {".txt", ".md", ".docx", ".xlsx", ".pptx"}:
        raise ValueError("Attachment type does not support bounded text extraction")
    data = _read_attachment_bytes(item, MAX_DOCUMENT_BYTES)
    text, outline = extract_text(Path(item["original_name"]), 120_001, source_bytes=data)
    current = require_attachment(attachment_id)
    if local_owner() != owner or current["chat_id"] != chat_id:
        raise HTTPException(404, "Вложение не найдено")
    return {
        "ok": True, "attachment_id": attachment_id, "chat_id": chat_id,
        "name": item["original_name"], "media_type": item["media_type"],
        "text": text[:120_000], "outline": outline, "truncated": len(text) > 120_000,
    }
