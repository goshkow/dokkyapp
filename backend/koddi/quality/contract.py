"""Единый результат объективного гейта качества и его хранение.

Статусы не смешиваются: passed / failed - измерение состоялось; skipped - не запускали по бюджету или ненужности;
unavailable - нет данных или инструмента (например, отчёта покрытия), это не ошибка кода; cancelled - отмена;
error - сбой самого гейта. Повтор того же прогона с тем же ключом (тот же код, политика и параметры) возвращает
сохранённый результат и не создаёт вторую запись. Полный отчёт лежит файлом в профиле, в модель идёт выжимка.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from koddi.config import data_dir
from koddi.storage.db import db

STATUSES = ("passed", "failed", "skipped", "unavailable", "cancelled", "error")
GATES = ("crap", "dry", "mutation", "architecture", "tests", "signals")
POLICY_VERSION = 1
MAX_STORED_FINDINGS = 200
MAX_HASHED_BYTES = 4 * 1024 * 1024
SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}


@dataclass
class GateResult:
    gate: str
    status: str
    scope: list[str] = field(default_factory=list)
    source_revision: str = ""
    baseline_revision: str | None = None
    started_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds"))
    duration_ms: int = 0
    checker: dict[str, Any] = field(default_factory=dict)
    findings: list[dict[str, Any]] = field(default_factory=list)
    summary: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    confidence: str = "exact"
    error: str | None = None
    policy_version: int = POLICY_VERSION
    run_id: int | None = None

    def __post_init__(self) -> None:
        if self.status not in STATUSES:
            raise ValueError(f"unknown gate status {self.status}")
        if self.gate not in GATES:
            raise ValueError(f"unknown gate {self.gate}")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class Timer:
    def __init__(self) -> None:
        self.started = time.monotonic()

    def ms(self) -> int:
        return int((time.monotonic() - self.started) * 1000)


def revision(root: Path, paths: list[str]) -> str:
    """Отпечаток содержимого файлов области: результаты привязаны к коду, а не ко времени."""
    digest = hashlib.sha256()
    for path in sorted(set(paths)):
        target = root / path
        try:
            if not target.is_file():
                data = b"\0missing"
            elif (stat := target.stat()).st_size > MAX_HASHED_BYTES:
                # большие файлы гейты всё равно не разбирают - хватит размера и времени изменения
                data = f"\0large:{stat.st_size}:{stat.st_mtime_ns}".encode()
            else:
                data = target.read_bytes()
        except OSError:
            data = b"\0unreadable"
        digest.update(path.encode() + b"\0" + hashlib.sha256(data).digest())
    return digest.hexdigest()[:24]


def run_key(gate: str, source_revision: str, options: dict[str, Any]) -> str:
    payload = json.dumps({"gate": gate, "rev": source_revision, "policy": POLICY_VERSION, "options": options},
                         sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode()).hexdigest()[:32]


def _reports_dir() -> Path:
    path = data_dir() / "quality-reports"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _write_report(run_id: int, payload: dict[str, Any]) -> Path:
    target = _reports_dir() / f"{run_id}.json"
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=target.parent, delete=False, suffix=".tmp") as handle:
        json.dump(payload, handle, ensure_ascii=False)
        temporary = Path(handle.name)
    os.replace(temporary, target)
    return target


def find(project_id: int, owner: str, key: str) -> dict[str, Any] | None:
    with db() as conn:
        row = conn.execute("SELECT * FROM quality_runs WHERE project_id=? AND owner_id=? AND idempotency_key=?",
                           (project_id, owner, key)).fetchone()
    return _row(row) if row else None


def persist(project_id: int, owner: str, turn_id: int | None, result: GateResult, key: str) -> GateResult:
    """Сохраняет итог; отменённый, сломанный и временно неизмеренный прогон не занимают ключ - повтор пересчитает."""
    # unavailable с причиной (красный базовый прогон, инфраструктура, обрезка по времени) может пройти при повторе;
    # без причины (нет отчёта покрытия) - стабилен: новое покрытие меняет ключ само
    transient = result.status == "unavailable" and (result.error or result.summary.get("truncated"))
    if result.status in {"cancelled", "error"} or transient:
        key = f"{key}:{result.status}:{time.time_ns()}"
    findings = sorted(result.findings, key=lambda item: (SEVERITY_ORDER.get(item.get("severity", "info"), 9),
                                                         -float(item.get("value") or 0)))
    result.findings = findings
    with db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        existing = conn.execute("SELECT id FROM quality_runs WHERE project_id=? AND owner_id=? AND idempotency_key=?",
                                (project_id, owner, key)).fetchone()
        if existing:
            result.run_id = int(existing["id"])
            conn.commit()
            return result
        turn = turn_id if turn_id is not None and conn.execute("SELECT 1 FROM turns WHERE id=?", (turn_id,)).fetchone() else None
        cursor = conn.execute(
            "INSERT INTO quality_runs(project_id,owner_id,turn_id,gate,status,idempotency_key,source_revision,"
            "baseline_revision,scope_json,checker_json,findings_json,summary_json,error,policy_version,started_at,duration_ms) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (project_id, owner, turn, result.gate, result.status, key, result.source_revision, result.baseline_revision,
             json.dumps(result.scope[:200], ensure_ascii=False), json.dumps(result.checker, ensure_ascii=False),
             json.dumps(findings[:MAX_STORED_FINDINGS], ensure_ascii=False),
             json.dumps({**result.summary, "notes": result.notes[:20], "confidence": result.confidence}, ensure_ascii=False),
             result.error, result.policy_version, result.started_at, result.duration_ms))
        result.run_id = int(cursor.lastrowid)
        path = _write_report(result.run_id, result.to_dict())
        conn.execute("UPDATE quality_runs SET report_path=? WHERE id=?", (str(path), result.run_id))
        conn.commit()
    return result


def _row(row) -> dict[str, Any]:
    item = dict(row)
    for column, default in (("scope_json", []), ("checker_json", {}), ("findings_json", []), ("summary_json", {})):
        try:
            item[column[:-5]] = json.loads(item.pop(column) or "null") or default
        except ValueError:
            item[column[:-5]] = default
    item.pop("report_path", None)
    item.pop("idempotency_key", None)
    return item


def latest(project_id: int, owner: str, *, gate: str | None = None, turn_id: int | None = None,
           limit: int = 20) -> list[dict[str, Any]]:
    query = "SELECT * FROM quality_runs WHERE project_id=? AND owner_id=?"
    params: list[Any] = [project_id, owner]
    if gate:
        query += " AND gate=?"
        params.append(gate)
    if turn_id is not None:
        query += " AND turn_id=?"
        params.append(turn_id)
    with db() as conn:
        rows = conn.execute(query + " ORDER BY id DESC LIMIT ?", (*params, max(1, min(limit, 200)))).fetchall()
    return [_row(row) for row in rows]


def report(run_id: int, owner: str, *, project_id: int | None = None) -> dict[str, Any] | None:
    """Полный отчёт прогона, только владельцу (и в рамках проекта, если он указан)."""
    with db() as conn:
        row = conn.execute("SELECT * FROM quality_runs WHERE id=? AND owner_id=?", (run_id, owner)).fetchone()
    if row is None or (project_id is not None and row["project_id"] != project_id):
        return None
    path = row["report_path"]
    if path:
        target = Path(path)
        if target.resolve().is_relative_to(_reports_dir().resolve()) and target.is_file():
            try:
                return json.loads(target.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                pass
    return {**_row(row), "findings_truncated": True}


def compact(result: GateResult, *, limit: int = 8) -> dict[str, Any]:
    """Выжимка для модели: статус, сводка и первые находки; полный отчёт - по run_id."""
    shown = result.findings[:limit]
    return {"gate": result.gate, "status": result.status, "run_id": result.run_id, "summary": result.summary,
            "findings": shown, "more_findings": max(0, len(result.findings) - len(shown)), "notes": result.notes[:6],
            "confidence": result.confidence, **({"error": result.error} if result.error else {})}


def cache_get(key: str, max_age_days: int = 30) -> dict[str, Any] | None:
    with db() as conn:
        row = conn.execute("SELECT value_json FROM quality_cache WHERE cache_key=? AND created_at>=datetime('now',?)",
                           (key, f"-{max_age_days} days")).fetchone()
    if row is None:
        return None
    try:
        return json.loads(row["value_json"])
    except ValueError:
        return None


def cache_put(key: str, value: dict[str, Any]) -> None:
    with db() as conn:
        conn.execute("INSERT INTO quality_cache(cache_key,value_json) VALUES (?,?) ON CONFLICT(cache_key) DO UPDATE SET "
                     "value_json=excluded.value_json,created_at=CURRENT_TIMESTAMP", (key, json.dumps(value, ensure_ascii=False)))
