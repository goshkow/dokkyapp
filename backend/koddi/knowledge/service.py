"""Знания проекта: заметки карты как структурированная долговременная память и их Markdown-копия.

Источник истины - таблица `project_map_notes` (владелец, проект, вид, цели в карте, происхождение, уверенность,
статус, отпечаток коммита). Markdown выгружается в данные приложения или, если пользователь выбрал, в папку
`project-knowledge/` проекта; коммиты не создаются. Правка файла вне Докки распознаётся по хешу и
переносится в заметку как правка человека - агент её больше не перезаписывает.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
from pathlib import Path
from typing import Any

from koddi.logs import log_event
from koddi.storage.db import db, get_setting

LOCATIONS = ("app", "workspace", "off")
WORKSPACE_FOLDER = "project-knowledge"
FOLDERS = {
    "overview": "", "architecture": "", "decision": "decisions", "module": "modules", "file": "modules",
    "api": "api", "dataflow": "data-flows", "convention": "conventions", "issue": "issues", "gotcha": "issues",
    "test": "tests", "progress": "progress", "topic": "topics",
}
SECTION_TITLES = {
    "overview": "Overview", "architecture": "Architecture", "decision": "Decisions (ADR)", "module": "Modules",
    "file": "Files", "api": "API contracts", "dataflow": "Data flows", "convention": "Conventions",
    "issue": "Known issues", "gotcha": "Pitfalls", "test": "Tests", "progress": "Tasks and progress", "topic": "Topics",
}
MAX_RESULTS = 20
_sync_lock = threading.Lock()


class KnowledgeError(RuntimeError):
    pass


def location(project_id: int) -> str:
    value = (get_setting("knowledge_storage", {}) or {}).get(str(project_id), "app")
    return value if value in LOCATIONS else "app"


def set_location(project_id: int, value: str) -> None:
    from koddi.storage.db import set_setting

    if value not in LOCATIONS:
        raise KnowledgeError(f"location must be one of {', '.join(LOCATIONS)}")
    current = dict(get_setting("knowledge_storage", {}) or {})
    current[str(project_id)] = value
    set_setting("knowledge_storage", current)


def folder(project_id: int, owner: str, root: Path, where: str) -> Path:
    if where == "workspace":
        target = (root / WORKSPACE_FOLDER).resolve()
        # ссылка project-knowledge наружу проекта не даёт писать за его пределы
        if not target.is_relative_to(root.resolve()):
            raise KnowledgeError(f"{WORKSPACE_FOLDER} points outside the project")
        return target
    from koddi.config import data_dir

    # у каждого аккаунта своя папка: чужие знания не смешиваются
    return data_dir() / "knowledge" / str(project_id) / hashlib.sha256(owner.encode("utf-8")).hexdigest()[:16]


def _contained(base: Path, path: Path) -> bool:
    """Файл экспорта лежит внутри папки знаний и сам не ссылка: через подложенную ссылку нельзя ни прочитать
    чужой файл в заметку, ни перезаписать или удалить что-то за пределами папки."""
    try:
        return not path.is_symlink() and path.parent.resolve().is_relative_to(base.resolve())
    except (OSError, RuntimeError):
        return False


def _terms(text: str) -> list[str]:
    return list(dict.fromkeys(re.findall(r"[^\W_]{3,}", text.casefold())))[:16]


def note_status(graph, note: dict) -> str:
    if note["id"] in graph.stale_notes:
        return "stale"
    missing = [item for item in note.get("targets", []) if not graph.has_node(item.get("ref", ""))]
    return "stale" if missing else (note.get("status") or "valid")


def _view(graph, note: dict, *, body: bool = True, limit: int = 1500) -> dict:
    item = {
        "id": note["id"], "kind": note["kind"], "title": note["title"], "status": note_status(graph, note),
        "source": note.get("source", "agent"), "confidence": note.get("confidence", "medium"),
        "targets": [target.get("ref") for target in note.get("targets", [])], "updated_at": note.get("updated_at"),
    }
    if note.get("basis"):
        item["basis"] = note["basis"]
    if note.get("fingerprint"):
        item["commit"] = note["fingerprint"]
    if body:
        text = note.get("body") or ""
        item["body"] = text[:limit] + ("…" if len(text) > limit else "")
    return item


def search(project_id: int, owner: str, root: Path, query: str, *, limit: int = 8, kinds: list[str] | None = None) -> dict:
    """Заметки по задаче: совпадение слов в заголовке и тексте, связь с файлами из запроса и свободная память."""
    from koddi.agents.memory import retrieve_memory
    from koddi.projectmap import service as map_service

    map_service.ensure(project_id, root, wait=10.0)
    graph = map_service.graph(project_id, owner)
    terms = _terms(query)
    mentioned = {node for word in re.findall(r"[\w./-]+\.\w+|[\w-]+/[\w./-]+", query) if (node := graph.resolve_ref(word))}
    scored = []
    for note in graph.notes:
        if kinds and note["kind"] not in kinds:
            continue
        title = note["title"].casefold()
        text = (note.get("body") or "").casefold()
        score = sum(3 for term in terms if term in title) + sum(1 for term in terms if term in text)
        targets = {item.get("ref") for item in note.get("targets", [])} | set(graph.note_targets.get(note["id"], []))
        if mentioned & targets:
            score += 4
        if note["kind"] in {"decision", "convention", "architecture"} and score:
            score += 1
        if score or not terms:
            scored.append((score, note))
    scored.sort(key=lambda item: (-item[0], -item[1]["id"]))
    notes = [_view(graph, note) for _, note in scored[: max(1, min(limit, MAX_RESULTS))]]
    memory = [{"id": item["id"], "kind": item.get("kind"), "content": item["content"][:800]}
              for item in retrieve_memory(project_id, query, limit=4)] if terms else []
    result: dict[str, Any] = {"ok": True, "notes": notes, "total_notes": len(graph.notes),
                              "notice": "Notes are hints from earlier tasks and the user; verify in the files. "
                                        "status=stale means linked files changed after the note was written."}
    if memory:
        result["memory"] = memory
        result["memory_notice"] = "free-form memory from earlier chats; unverified"
    return result


def get(project_id: int, owner: str, note_id: int) -> dict:
    from koddi.projectmap import service as map_service

    graph = map_service.graph(project_id, owner)
    note = graph.notes_by_id.get(note_id)
    if note is None:
        raise KnowledgeError("note not found")
    backlinks = [other["id"] for other in graph.notes if f"note:{note_id}" in graph.note_targets.get(other["id"], [])]
    links = graph.note_targets.get(note_id, [])
    return {"ok": True, "note": _view(graph, note, limit=4000), "links": links,
            "backlinks": [_view(graph, graph.notes_by_id[item], body=False) for item in backlinks]}


def reconcile(project_id: int, owner: str, root: Path, *, apply: bool = False) -> dict:
    """Устаревшие заметки: пропавшие цели ищутся по имени файла (переименование), изменённые - на перепроверку."""
    from koddi.projectmap import service as map_service

    map_service.ensure(project_id, root, wait=20.0)
    graph = map_service.graph(project_id, owner)
    report = []
    fixed = 0
    for note in graph.notes:
        problems, renames = [], {}
        for target in note.get("targets", []):
            ref = target.get("ref", "")
            if graph.has_node(ref):
                if note["id"] in graph.stale_notes and ref.startswith("file:") and target.get("sha256") and \
                        graph.files.get(ref[5:], {}).get("sha256") != target["sha256"]:
                    problems.append({"target": ref, "problem": "file changed since the note was written"})
                continue
            if ref.startswith("file:"):
                name = Path(ref[5:]).name.lower()
                matches = [path for path in graph.by_stem.get(name, []) if Path(path).name.lower() == name]
                if len(matches) == 1:
                    renames[ref] = "file:" + matches[0]
                    problems.append({"target": ref, "problem": "file moved", "candidate": matches[0]})
                    continue
                problems.append({"target": ref, "problem": "file not found", **({"candidates": matches[:5]} if matches else {})})
            else:
                problems.append({"target": ref, "problem": "target not found"})
        if not problems:
            continue
        entry = {"id": note["id"], "title": note["title"], "kind": note["kind"], "source": note.get("source"),
                 "problems": problems}
        if apply and renames and note.get("source") != "user":
            refs = [renames.get(item.get("ref"), item.get("ref")) for item in note.get("targets", [])]
            refs = [ref[5:] if ref.startswith("file:") else ref for ref in refs if graph.has_node(ref)]
            body = note.get("body") or ""
            for old, new in renames.items():
                body = body.replace(f"[[{old[5:]}]]", f"[[{new[5:]}]]")
            map_service.save_note(project_id, owner, kind=note["kind"], title=note["title"], body=body, targets=refs,
                                  note=note["id"], confidence=note.get("confidence") or "medium",
                                  basis=(note.get("basis") or "") + " (targets updated after a move)", root=root)
            entry["fixed"] = sorted(renames.values())
            fixed += 1
        report.append(entry)
    if fixed:
        sync_after_change(project_id, owner, root)
    return {"ok": True, "notes": report, "fixed": fixed,
            "hint": "Fix or delete the remaining notes with project.map_update; user-edited notes need the user's decision."
            if report else "All notes are consistent with the code."}


# ---------- Markdown

def _slug(title: str) -> str:
    value = re.sub(r"[^\w\s-]", "", title.casefold(), flags=re.UNICODE)
    return re.sub(r"[\s_-]+", "-", value).strip("-")[:60] or "note"


def _render(graph, note: dict) -> str:
    view = _view(graph, note, body=False)
    meta = {"id": note["id"], "kind": note["kind"], "status": view["status"], "source": view["source"],
            "confidence": view["confidence"], "targets": view["targets"], "updated": note.get("updated_at")}
    if note.get("basis"):
        meta["basis"] = note["basis"]
    if note.get("fingerprint"):
        meta["commit"] = note["fingerprint"]
    links = []
    for target in view["targets"]:
        kind, _, key = target.partition(":")
        if kind == "note" and key.isdigit() and int(key) in graph.notes_by_id:
            links.append(f"[[{graph.notes_by_id[int(key)]['title']}]]")
        elif kind == "file":
            links.append(f"`{key}`")
        else:
            links.append(f"`{target}`")
    head = "---\n" + "\n".join(f"{key}: {json.dumps(value, ensure_ascii=False)}" for key, value in meta.items()) + "\n---\n"
    tail = ("\n\nLinks: " + ", ".join(links)) if links else ""
    return f"{head}# {note['title']}\n\n{(note.get('body') or '').strip()}{tail}\n"


def _parse(text: str) -> dict | None:
    match = re.match(r"^---\n(.*?)\n---\n#\s+(.+?)\n\n?(.*)$", text.replace("\r\n", "\n"), re.S)
    if not match:
        return None
    meta = {}
    for line in match.group(1).splitlines():
        key, _, value = line.partition(":")
        try:
            meta[key.strip()] = json.loads(value.strip())
        except ValueError:
            meta[key.strip()] = value.strip()
    body = re.sub(r"\n\nLinks: .*$", "", match.group(3).rstrip("\n"), flags=re.S)
    return {"meta": meta, "title": match.group(2).strip(), "body": body.strip()}


def _note_path(note: dict) -> str:
    sub = FOLDERS.get(note["kind"], "topics")
    name = f"{note['id']:04d}-{_slug(note['title'])}.md"
    return f"{sub}/{name}" if sub else name


def export_sync(project_id: int, owner: str, root: Path, where: str | None = None) -> dict:
    """Переносит правки Markdown, сделанные вне Докки, в заметки и выгружает все заметки и оглавление."""
    from koddi.projectmap import service as map_service

    where = where or location(project_id)
    if where == "off":
        return {"ok": True, "location": "off", "written": 0, "imported": 0}
    base = folder(project_id, owner, root, where)
    with _sync_lock:
        graph = map_service.graph(project_id, owner)
        with db() as conn:
            exported = {row["note_id"]: dict(row) for row in conn.execute(
                "SELECT note_id,path,sha256 FROM knowledge_exports WHERE project_id=? AND owner_id=? AND location=?",
                (project_id, owner, where))}
        imported = []
        for note_id, record in exported.items():
            path = base / record["path"]
            if note_id not in graph.notes_by_id or not _contained(base, path) or not path.is_file():
                continue
            data = path.read_bytes()
            if hashlib.sha256(data).hexdigest() == record["sha256"]:
                continue
            parsed = _parse(data.decode("utf-8", errors="replace"))
            note = graph.notes_by_id[note_id]
            if parsed is None or parsed["meta"].get("id") != note_id:
                log_event("WARNING", "knowledge.import_skipped", "Markdown note has an unknown format", {"path": record["path"]})
                continue
            if parsed["title"] != note["title"] or parsed["body"] != (note.get("body") or "").strip():
                refs = [item.get("ref", "") for item in note.get("targets", [])]
                refs = [ref[5:] if ref.startswith("file:") else ref for ref in refs if graph.has_node(ref)]
                map_service.save_note(project_id, owner, kind=note["kind"], title=parsed["title"], body=parsed["body"],
                                      targets=refs, note=note_id, source="user", confidence="high",
                                      basis="edited by the user in Markdown", root=root)
                imported.append(note_id)
        if imported:
            graph = map_service.graph(project_id, owner)
        base.mkdir(parents=True, exist_ok=True)
        written = 0
        rows = []
        index: dict[str, list[str]] = {}
        for note in graph.notes:
            relative = _note_path(note)
            content = _render(graph, note).encode("utf-8")
            target = base / relative
            previous = exported.get(note["id"])
            if previous and previous["path"] != relative and _contained(base, base / previous["path"]):
                (base / previous["path"]).unlink(missing_ok=True)
            if not _contained(base, target):
                log_event("WARNING", "knowledge.export_skipped", "note path is a link or leads outside the folder",
                          {"path": relative})
                continue
            if not target.is_file() or target.read_bytes() != content:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(content)
                written += 1
            rows.append((project_id, owner, note["id"], where, relative, hashlib.sha256(content).hexdigest()))
            index.setdefault(note["kind"], []).append(f"- [[{note['title']}]] ({note_status(graph, note)}) - `{relative}`")
        readme = ["# Project knowledge", "",
                  "Generated by Dokky from project notes. Edit a note file and Dokky imports the change; "
                  "do not store secrets here.", ""]
        for kind in SECTION_TITLES:
            if index.get(kind):
                readme.extend([f"## {SECTION_TITLES[kind]}", "", *index[kind], ""])
        if _contained(base, base / "README.md"):
            (base / "README.md").write_text("\n".join(readme), encoding="utf-8")
        alive = {note["id"] for note in graph.notes}
        for note_id, record in exported.items():
            if note_id not in alive and _contained(base, base / record["path"]):
                (base / record["path"]).unlink(missing_ok=True)
        with db() as conn:
            conn.execute("DELETE FROM knowledge_exports WHERE project_id=? AND owner_id=? AND location=?", (project_id, owner, where))
            conn.executemany(
                "INSERT INTO knowledge_exports(project_id,owner_id,note_id,location,path,sha256) VALUES (?,?,?,?,?,?)", rows)
    return {"ok": True, "location": where, "folder": str(base), "written": written, "imported": imported, "notes": len(rows)}


def sync_after_change(project_id: int, owner: str, root: Path | None) -> None:
    """Обновление Markdown после изменения заметок; ошибка записи не мешает работе агента."""
    if root is None or location(project_id) == "off":
        return
    try:
        export_sync(project_id, owner, root)
    except (OSError, KnowledgeError, ValueError) as exc:
        log_event("WARNING", "knowledge.sync_failed", str(exc)[:300], {"project_id": project_id})


def knowledge_tool(project_id: int | None, workspace: Path, args: dict, *, owner: str) -> dict:
    from koddi.projectmap.service import project_for_workspace

    project_id = project_id or project_for_workspace(workspace)
    if project_id is None:
        return {"ok": False, "error": "project knowledge needs a project opened in Dokky"}
    action = args.get("action") or "search"
    try:
        if action == "search":
            return search(project_id, owner, workspace, str(args.get("query") or ""), limit=int(args.get("limit") or 8),
                          kinds=list(args.get("kinds") or []) or None)
        if action == "export":
            return export_sync(project_id, owner, workspace)
        if action == "get":
            if not args.get("note_id"):
                raise KnowledgeError("note_id is required")
            return get(project_id, owner, int(args["note_id"]))
        if action == "reconcile":
            return reconcile(project_id, owner, workspace, apply=bool(args.get("apply")))
        raise KnowledgeError("action must be search, get, reconcile or export")
    except KnowledgeError as exc:
        return {"ok": False, "error": str(exc), "retryable": True}
