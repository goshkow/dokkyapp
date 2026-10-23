from __future__ import annotations

import io
import json
import re
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

from fastapi import HTTPException

from koddi.policy.account import local_owner, owns
from koddi.storage.db import db


MAX_DOCUMENT_BYTES = 8 * 1024 * 1024
MAX_EXPANDED_BYTES = 16 * 1024 * 1024
MAX_XML_BYTES = 2 * 1024 * 1024
MAX_ARCHIVE_MEMBERS = 512
MAX_OUTLINE_ITEMS = 128
_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
_S = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"


class _Text:
    def __init__(self, limit: int):
        self.limit = limit
        self.length = 0
        self.parts: list[str] = []

    @property
    def full(self) -> bool:
        return self.length >= self.limit

    def add(self, value: str) -> None:
        if self.full or not value:
            return
        part = (("\n" if self.parts else "") + value)[:self.limit - self.length]
        self.parts.append(part)
        self.length += len(part)

    def text(self) -> str:
        return "".join(self.parts)


def _decode_text(data: bytes) -> str:
    try:
        text = data.decode("utf-8-sig")
    except UnicodeError as exc:
        raise ValueError("Document must contain UTF-8 text") from exc
    if any(ord(char) < 32 and char not in "\t\r\n" for char in text):
        raise ValueError("Binary content is not a text document")
    return text


def _xml(archive: zipfile.ZipFile, name: str) -> ET.Element:
    info = archive.getinfo(name)
    if info.file_size > MAX_XML_BYTES:
        raise ValueError("Document XML exceeds size limit")
    with archive.open(info) as handle:
        data = handle.read(MAX_XML_BYTES + 1)
    if len(data) > MAX_XML_BYTES:
        raise ValueError("Document XML exceeds size limit")
    text = _decode_text(data)
    if "<!DOCTYPE" in text.upper() or "<!ENTITY" in text.upper():
        raise ValueError("Document XML entities are not allowed")
    parser = ET.XMLPullParser(events=("start", "end"))
    root = None
    depth = nodes = 0
    for offset in range(0, len(text), 4096):
        parser.feed(text[offset:offset + 4096])
        for event, element in parser.read_events():
            if event == "start":
                root = element if root is None else root
                depth += 1
                nodes += 1
                if depth > 64 or nodes > 50_000:
                    raise ValueError("Document XML structure exceeds limit")
            else:
                depth -= 1
    parser.close()
    if root is None:
        raise ValueError("Empty document XML")
    return root


def _check_archive(archive: zipfile.ZipFile) -> set[str]:
    members = archive.infolist()
    if len(members) > MAX_ARCHIVE_MEMBERS:
        raise ValueError("Document has too many archive members")
    total = 0
    names: set[str] = set()
    for member in members:
        name = member.filename
        total += member.file_size
        if (
            name in names or len(name) > 255 or name.startswith("/") or "\\" in name
            or ".." in name.split("/") or member.flag_bits & 1
            or member.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}
            or total > MAX_EXPANDED_BYTES
            or member.file_size > max(1, member.compress_size) * 100
        ):
            raise ValueError("Unsafe document archive")
        names.add(name)
    return names


def _office_text(data: bytes, suffix: str, max_chars: int) -> tuple[str, list[dict]]:
    output = _Text(max_chars)
    outline: list[dict] = []
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            names = _check_archive(archive)
            if suffix == ".docx":
                root = _xml(archive, "word/document.xml")
                for paragraph in root.iter(f"{_W}p"):
                    text = "".join(node.text or "" for node in paragraph.iter(f"{_W}t"))
                    output.add(text)
                    style = paragraph.find(f"{_W}pPr/{_W}pStyle")
                    if (
                        style is not None and style.get(f"{_W}val", "").lower().startswith("heading")
                        and text and len(outline) < MAX_OUTLINE_ITEMS
                    ):
                        outline.append({"level": 1, "title": text[:120]})
                    if output.full:
                        break
            elif suffix == ".pptx":
                slides = sorted(
                    (name for name in names if re.fullmatch(r"ppt/slides/slide[1-9][0-9]*\.xml", name)),
                    key=lambda name: int(re.search(r"slide([0-9]+)\.xml$", name)[1]),
                )
                for index, name in enumerate(slides):
                    root = _xml(archive, name)
                    for part in root.iter(f"{_A}t"):
                        output.add(part.text or "")
                        if output.full:
                            break
                    if len(outline) < MAX_OUTLINE_ITEMS:
                        outline.append({"level": 1, "title": f"Slide {index + 1}"})
                    if output.full:
                        break
            else:
                shared = []
                if "xl/sharedStrings.xml" in names:
                    root = _xml(archive, "xl/sharedStrings.xml")
                    for item in root.iter(f"{_S}si"):
                        if len(shared) >= 20_000:
                            raise ValueError("Document has too many shared strings")
                        shared.append("".join(node.text or "" for node in item.iter(f"{_S}t")))
                sheets = sorted(
                    name for name in names
                    if re.fullmatch(r"xl/worksheets/sheet[1-9][0-9]*\.xml", name)
                )
                if len(sheets) > 32:
                    raise ValueError("Document has too many sheets")
                for index, name in enumerate(sheets):
                    root = _xml(archive, name)
                    outline.append({"level": 1, "title": f"Sheet {index + 1}"})
                    for row in root.iter(f"{_S}row"):
                        values = []
                        row_chars = 0
                        remaining = max_chars - output.length
                        for cell in row.findall(f"{_S}c"):
                            value = cell.findtext(f"{_S}v", "")
                            if cell.get("t") == "s":
                                if not value.isdecimal() or len(value) > 8 or int(value) >= len(shared):
                                    raise ValueError("Invalid shared string reference")
                                value = shared[int(value)]
                            elif cell.get("t") == "inlineStr":
                                value = "".join(node.text or "" for node in cell.iter(f"{_S}t"))
                            value = value[:max(0, remaining - row_chars)]
                            values.append(value)
                            row_chars += len(value) + 1
                            if row_chars >= remaining:
                                break
                        output.add("\t".join(values))
                        if output.full:
                            break
                    if output.full:
                        break
            if not output.parts and suffix != ".docx":
                raise ValueError("Document contains no readable text")
    except (zipfile.BadZipFile, ET.ParseError, KeyError, RuntimeError, NotImplementedError) as exc:
        raise ValueError("Invalid document archive") from exc
    return output.text(), outline


def extract_text(
    path: Path, max_chars: int = 200_000, *, source_bytes: bytes | None = None,
) -> tuple[str, list[dict]]:
    if not isinstance(max_chars, int) or not 1 <= max_chars <= 200_000:
        raise ValueError("Invalid document character limit")
    suffix = path.suffix.lower()
    text_types = {".txt", ".md", ".py", ".js", ".ts", ".json", ".xml", ".css", ".html", ".htm"}
    if suffix not in text_types | {".docx", ".xlsx", ".pptx"}:
        # PDF требует отдельного изолированного парсера с лимитом памяти на распаковку
        raise ValueError("Document type does not support bounded text extraction")
    if source_bytes is None:
        with path.open("rb") as handle:
            source_bytes = handle.read(MAX_DOCUMENT_BYTES + 1)
    if len(source_bytes) > MAX_DOCUMENT_BYTES:
        raise ValueError("Document exceeds input byte limit")
    default_outline = [{"level": 1, "title": path.name[:255]}]
    if suffix in {".docx", ".xlsx", ".pptx"}:
        text, outline = _office_text(source_bytes, suffix, max_chars)
        return text, outline or default_outline
    text = _decode_text(source_bytes)
    if suffix in {".html", ".htm"}:
        if len(source_bytes) > MAX_XML_BYTES:
            raise ValueError("HTML exceeds size limit")
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(text, "html.parser")
        for tag in soup(["script", "style", "noscript"]):
            tag.decompose()
        output = _Text(max_chars)
        for part in soup.stripped_strings:
            output.add(part)
            if output.full:
                break
        text = output.text()
    return text[:max_chars], default_outline


def index_document(source_path: str, attachment_id: int | None = None) -> dict:
    from koddi.attachments.service import _read_attachment_bytes, require_attachment

    owner = local_owner()
    if attachment_id is None:
        raise HTTPException(422, "Для индексации нужно собственное вложение")
    item = require_attachment(attachment_id)
    path = Path(item["stored_path"])
    if Path(source_path) != path or not owns(item["owner_id"], owner):
        raise HTTPException(404, "Вложение не найдено")
    data = _read_attachment_bytes(item, MAX_DOCUMENT_BYTES)
    text, outline = extract_text(Path(item["original_name"]), source_bytes=data)
    with db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        current = require_attachment(attachment_id)
        if local_owner() != owner or current != item:
            raise HTTPException(404, "Вложение не найдено")
        cur = conn.execute(
            """INSERT INTO documents(attachment_id, source_path, kind, title, outline_json, summary)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (attachment_id, str(path), path.suffix.lower().lstrip("."), item["original_name"],
             json.dumps(outline, ensure_ascii=False), text[:2000]),
        )
        doc_id = int(cur.lastrowid)
        for idx in range(0, len(text), 8000):
            chunk = text[idx:idx + 8000]
            conn.execute(
                "INSERT INTO document_chunks(document_id, chunk_index, content, token_estimate) VALUES (?, ?, ?, ?)",
                (doc_id, idx // 8000, chunk, max(1, len(chunk) // 4)),
            )
        row = conn.execute("SELECT * FROM documents WHERE id=?", (doc_id,)).fetchone()
    return {**dict(row), "owner_id": item["owner_id"]}


def list_documents() -> list[dict]:
    owner = local_owner()
    legacy_owner = "" if owns("", owner) else owner
    with db() as conn:
        rows = conn.execute(
            """SELECT d.*, a.owner_id FROM documents d
               JOIN attachments a ON a.id=d.attachment_id LEFT JOIN chats c ON c.id=a.chat_id
               WHERE a.status<>'deleted' AND a.owner_id IN (?, ?)
                 AND (a.chat_id IS NULL OR c.owner_id IN (?, ?))
               ORDER BY d.id DESC LIMIT 200""",
            (owner, legacy_owner, owner, legacy_owner),
        ).fetchall()
    if local_owner() != owner:
        raise HTTPException(404, "Документ не найден")
    return [dict(row) for row in rows]
