"""Качество хода: контракт задачи, прогоны проверок, ревью и решение перед task.complete.

Состояния: implemented → verification_pending → verifying → failed/repairing → verified | unverified | blocked.
Гейт включается только в сессиях, где у модели есть qa.run (старые сессии с прежним набором инструментов
не меняются). Цикл ограничен: после MAX_REPAIR неудачных прогонов или повтора одинакового падения
и после MAX_GATE_BLOCKS отказов завершение пропускается с честным статусом в отчёте, а не зелёной галочкой.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from koddi.storage.db import db

MAX_REPAIR = 3
MAX_GATE_BLOCKS = 3
MAX_REVIEW_ROUNDS = 2
GATED_MODES = {"development", "debug", "build"}
CONTRACT_LISTS = ("scope", "non_goals", "assumptions", "acceptance", "risks", "affected_components", "test_plan")
EVIDENCE_STATUSES = {"met", "not_met", "unverified"}
STATE_LABELS = {
    "verified": "проверено",
    "unverified": "не проверено",
    "failed": "проверки не прошли",
    "blocked": "заблокировано",
    "verification_pending": "проверки не запускались",
}


# ---------- хранение

def load(turn_id: int | None) -> dict[str, Any]:
    empty = {"state": "implemented", "contract": {}, "evidence": [], "qa": {}, "qa_runs": 0, "qa_fingerprint": None,
             "failure_signatures": [], "review": {}, "review_rounds": 0, "gate_blocks": 0, "checkpoint": {}}
    if turn_id is None:
        return empty
    with db() as conn:
        row = conn.execute("SELECT * FROM turn_quality WHERE turn_id=?", (turn_id,)).fetchone()
    if row is None:
        return empty
    result = dict(empty)
    for key in ("state", "qa_runs", "qa_fingerprint", "review_rounds", "gate_blocks"):
        result[key] = row[key]
    for key, column in (("contract", "contract_json"), ("evidence", "evidence_json"), ("qa", "qa_json"),
                        ("failure_signatures", "failure_signatures_json"), ("review", "review_json"),
                        ("checkpoint", "checkpoint_json")):
        try:
            result[key] = json.loads(row[column] or "null") or empty[key]
        except ValueError:
            pass
    return result


def save(turn_id: int | None, **fields: Any) -> None:
    if turn_id is None or not fields:
        return
    columns = {"contract": "contract_json", "evidence": "evidence_json", "qa": "qa_json",
               "failure_signatures": "failure_signatures_json", "review": "review_json", "checkpoint": "checkpoint_json"}
    values = {columns.get(key, key): json.dumps(value, ensure_ascii=False) if key in columns else value for key, value in fields.items()}
    names = list(values)
    with db() as conn:
        if conn.execute("SELECT 1 FROM turns WHERE id=?", (turn_id,)).fetchone() is None:
            return
        conn.execute(
            f"INSERT INTO turn_quality(turn_id,{','.join(names)}) VALUES (?,{','.join('?' for _ in names)}) "
            f"ON CONFLICT(turn_id) DO UPDATE SET {','.join(f'{name}=excluded.{name}' for name in names)},updated_at=CURRENT_TIMESTAMP",
            (turn_id, *values.values()),
        )


# ---------- контракт и доказательства

def _texts(value: Any, *, limit: int = 30, size: int = 500) -> list[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    return [" ".join(str(item).split())[:size] for item in value if str(item or "").strip()][:limit]


def normalize_contract(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    contract: dict[str, Any] = {}
    goal = " ".join(str(value.get("goal") or "").split())[:2000]
    if goal:
        contract["goal"] = goal
    for key in CONTRACT_LISTS:
        items = _texts(value.get(key))
        if items:
            contract[key] = items
    return contract


def save_contract(turn_id: int | None, value: Any) -> dict[str, Any]:
    contract = normalize_contract(value)
    if contract:
        save(turn_id, contract=contract)
    return contract


def inherit_contract(chat_id: int, turn_id: int) -> dict[str, Any]:
    """Незавершённый план переходит в новый ход вместе с контрактом (после сжатия, перезапуска, продолжения)."""
    with db() as conn:
        row = conn.execute(
            "SELECT q.contract_json FROM turn_quality q JOIN turns t ON t.id=q.turn_id WHERE t.chat_id=? AND t.id<? "
            "AND q.contract_json<>'{}' ORDER BY t.id DESC LIMIT 1", (chat_id, turn_id)).fetchone()
    if row is None:
        return {}
    try:
        contract = normalize_contract(json.loads(row["contract_json"]))
    except ValueError:
        return {}
    if contract:
        save(turn_id, contract=contract)
    return contract


def normalize_evidence(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    items = []
    for item in value[:40]:
        if not isinstance(item, dict) or not str(item.get("criterion") or "").strip():
            continue
        status = str(item.get("status") or "unverified")
        items.append({
            "criterion": " ".join(str(item["criterion"]).split())[:500],
            "status": status if status in EVIDENCE_STATUSES else "unverified",
            "proof": " ".join(str(item.get("proof") or "").split())[:500],
            "check": str(item.get("check") or "")[:200],
        })
    return items


def judge_evidence(evidence: list[dict], qa: dict, current: bool) -> list[dict]:
    """«Выполнено» засчитывается, только если указана прошедшая проверка Докки на текущем коде."""
    passed = {item["id"] for item in qa.get("results", []) if item.get("status") == "passed"} if current else set()
    verified_ui = set(qa.get("ui_passed") or []) if current else set()
    judged = []
    for item in evidence:
        verdict = item["status"]
        if verdict == "met":
            verdict = "verified" if item["check"] and (item["check"] in passed or item["check"] in verified_ui) else "claimed"
        judged.append({**item, "verdict": verdict})
    return judged


# ---------- изменения хода

def turn_changes(turn_id: int | None) -> dict[str, str | None]:
    """Файлы проекта, изменённые инструментами в этом ходе (не откатанные), и их текст до хода."""
    if turn_id is None:
        return {}
    with db() as conn:
        rows = conn.execute(
            "SELECT f.path,f.before_text FROM file_changes f JOIN change_sets c ON c.id=f.change_set_id "
            "WHERE c.turn_id=? AND c.status='applied' AND c.project_id IS NOT NULL ORDER BY f.id", (turn_id,)).fetchall()
    first: dict[str, str | None] = {}
    for row in rows:
        first.setdefault(row["path"], row["before_text"])
    return first


def fingerprint(root: Path, paths: list[str]) -> str:
    digest = hashlib.sha256()
    for path in sorted(paths):
        target = root / path
        try:
            data = target.read_bytes() if target.is_file() else b"\0missing"
        except OSError:
            data = b"\0unreadable"
        digest.update(path.encode() + b"\0" + hashlib.sha256(data).digest())
    return digest.hexdigest()[:24]


# ---------- проверки

def record_qa(turn_id: int | None, root: Path, *, scope: str, results: list[dict], notes: list[str], paths: list[str],
              detected: set[str] | None = None) -> dict[str, Any]:
    from koddi.quality.checks import overall, summarize

    state = load(turn_id)
    current = fingerprint(root, paths)
    previous = state["qa"] if state.get("qa_fingerprint") == current else {}
    before = {item["id"]: item["status"] for item in previous.get("results", [])}
    signatures = list(state["failure_signatures"])
    repeated = []
    for item in results:
        if item["status"] == "passed" and before.get(item["id"]) == "failed":
            item["flaky"] = True  # упало и прошло на том же коде
        if item.get("signature"):
            if item["signature"] in signatures:
                item["repeated"] = True
                repeated.append(item["id"])
            signatures.append(item["signature"])
    status = overall(results)
    # каждая проверка помнит версию кода, на которой шла: зелёный повтор части проверок после правки
    # не доказывает, что остальные (линт, типы) на новом коде тоже проходят
    known = {item["id"]: dict(item) for item in state.get("qa", {}).get("checks_seen", []) if item.get("id")}
    for item in results:
        known[item["id"]] = {"id": item["id"], "status": item["status"], "fingerprint": current}
    # проверка, которой больше нет в проекте (удалили конфиг), не держит ход непроверенным
    stale = sorted(check for check, item in known.items()
                   if item["fingerprint"] != current and item["status"] in {"passed", "failed"}
                   and (detected is None or check in detected))
    if stale:
        notes = [*notes, "на текущем коде не перезапускались: " + ", ".join(stale[:10])
                 + " - запусти qa.run без checks, чтобы доказать весь набор"]
    failed_runs = int(state.get("qa", {}).get("failed_runs") or 0) + (status == "failed")
    stored_results = [{key: value for key, value in item.items() if key != "output_tail"} for item in results]
    qa = {"status": status, "scope": scope, "summary": summarize(results), "results": stored_results, "notes": notes,
          "failed_runs": failed_runs, "repeated": repeated, "ui_passed": state.get("qa", {}).get("ui_passed", []),
          "ui": state.get("qa", {}).get("ui", []), "checks_seen": list(known.values())[-40:], "stale": stale}
    if status == "passed" and stale:
        new_state = "verification_pending"
    elif status == "passed":
        new_state = "verified"
    elif status == "failed":
        new_state = "failed" if failed_runs >= MAX_REPAIR or repeated else "repairing"
    elif status == "cancelled":
        new_state = "verification_pending"
    else:
        new_state = "unverified"
    save(turn_id, qa=qa, qa_runs=int(state["qa_runs"]) + 1, qa_fingerprint=current, failure_signatures=signatures[-60:],
         state=new_state)
    return {**qa, "state": new_state, "attempt": failed_runs, "max_attempts": MAX_REPAIR}


def record_ui(turn_id: int | None, root: Path, check_id: str, report: dict) -> None:
    """Результат ui.verify попадает в доказательства хода; засчитывается, пока код не менялся."""
    if turn_id is None:
        return
    state = load(turn_id)
    qa = dict(state["qa"])
    ui = [item for item in qa.get("ui", []) if item.get("id") != check_id][-9:]
    ui.append({"id": check_id, "status": report.get("status"), "summary": report.get("summary", "")[:300]})
    qa["ui"] = ui
    qa["ui_passed"] = [item["id"] for item in ui if item["status"] == "passed"]
    save(turn_id, qa=qa)


def qa_tool(runner, args: dict[str, Any]) -> dict[str, Any]:
    from koddi.codeintel.impact import changed_files
    from koddi.quality.checks import DEFAULT_TIMEOUT, as_dict, detect, run

    root = runner._require_workspace()
    scope = "full" if args.get("scope") == "full" else "changed"
    explicit = [str(path).replace("\\", "/") for path in args.get("paths") or []][:200]
    changes = turn_changes(runner.turn_id)
    paths = explicit or [path for path in changes if (root / path).is_file()] or changed_files(root)
    checks, notes = detect(root, paths, scope=scope, project_id=runner.project_id)
    detected = {check.id for check in checks}
    wanted = {str(item) for item in args.get("checks") or []}
    if wanted:
        unknown = sorted(wanted - {check.id for check in checks})
        checks = [check for check in checks if check.id in wanted]
        if unknown:
            notes.append("неизвестные проверки пропущены: " + ", ".join(unknown[:5]))
    if args.get("detect_only"):
        return {"ok": True, "scope": scope, "paths": paths[:50], "checks": [as_dict(check) for check in checks], "notes": notes}
    ub = getattr(runner, "ub_workflow", False)
    if ub:
        from koddi.quality import workflow

        blocked = workflow.premise_block(runner.turn_id, [check.id for check in checks] if wanted else None)
        if blocked:
            return {"ok": False, "status": "premise_required", "error": blocked}
    timeout = max(30, min(int(args.get("timeout_seconds") or DEFAULT_TIMEOUT), 1800))
    fingerprint_paths = [path for path in changes] or paths
    if args.get("expect") == "fail":
        # ожидаемо красный прогон не считается попыткой исправления и не меняет состояние проверки хода
        from koddi.quality import workflow

        results = run(runner, checks, timeout=timeout)
        verdict = workflow.red_verdict(results, str(args.get("reason") or "") or None)
        workflow.record_red(runner.turn_id, runner.project_id, fingerprint(root, fingerprint_paths), verdict, args.get("reason"))
        return {"ok": verdict["verified"], "status": "red_verified" if verdict["verified"] else "red_not_verified",
                "red": verdict, "results": results, "notes": notes,
                "guidance": "Now make the smallest change that turns this test green, then run qa.run normally."
                if verdict["verified"] else "This is not a valid red: " + str(verdict.get("why"))}
    if checks:
        save(runner.turn_id, state="verifying")
    results = run(runner, checks, timeout=timeout)
    report = record_qa(runner.turn_id, root, scope=scope, results=results, notes=notes, paths=fingerprint_paths,
                       detected=detected)
    cycle: dict[str, Any] = {}
    if ub:
        cycle = workflow.record_checks(runner.turn_id, fingerprint(root, fingerprint_paths), results)
    guidance = None
    if report["status"] == "failed":
        guidance = ("Fix the specific defects listed in diagnostics (smallest change), then run qa.run again with "
                    "checks=[failed ids]; once they pass, finish with qa.run without checks so the whole set is proven "
                    "on the final code." if report["state"] == "repairing" else
                    "Repair limit reached or the same failure repeated: stop changing code for this, report the failure "
                    "honestly in task.complete.")
    elif report["status"] == "passed" and report.get("stale"):
        guidance = ("These checks passed, but " + ", ".join(report["stale"][:5]) + " ran on an older version of the code. "
                    "Run qa.run without checks to prove the whole set on the current code.")
    elif report["status"] in {"unverified", "no_checks", "partial"}:
        guidance = "Some checks could not run (see notes and statuses); say so in the final report, do not claim them."
    if cycle.get("premise_required"):
        guidance = workflow.premise_block(runner.turn_id, None)
    return {"ok": report["status"] in {"passed", "partial", "unverified", "no_checks"}, "status": report["status"],
            "state": report["state"], "summary": report["summary"], "results": results, "notes": notes,
            "attempt": report["attempt"], "max_attempts": MAX_REPAIR, **({"stale": report["stale"]} if report.get("stale") else {}),
            **cycle, **({"guidance": guidance} if guidance else {})}


# ---------- решение перед завершением

@dataclass
class GateDecision:
    allow: bool
    state: str
    error: str | None = None
    report: str = ""
    details: dict[str, Any] = field(default_factory=dict)
    review_request: dict[str, Any] | None = None


def _qa_allowed(workspace: Path, project_id: int | None) -> bool:
    from koddi.policy.engine import decide

    return decide("qa.run", {}, workspace, project_id).action != "block"


def evaluate(*, turn_id: int | None, workspace: Path | None, project_id: int | None, mode: str, tool_names: set[str],
             evidence: Any, review_enabled: bool) -> GateDecision:
    if turn_id is None or workspace is None or mode not in GATED_MODES or "qa.run" not in tool_names:
        return GateDecision(True, "implemented")
    state = load(turn_id)
    items = normalize_evidence(evidence)
    if items:
        save(turn_id, evidence=items)
    else:
        items = state["evidence"]
    changes = turn_changes(turn_id)
    contract = state["contract"]
    if not changes and not contract.get("acceptance"):
        return GateDecision(True, "implemented")
    blocks = int(state["gate_blocks"])
    exhausted = blocks >= MAX_GATE_BLOCKS
    current = bool(changes) and state.get("qa_fingerprint") == fingerprint(workspace, list(changes))
    qa = state["qa"] if current else {}
    status = qa.get("status")

    def block(message: str, **details) -> GateDecision:
        save(turn_id, gate_blocks=blocks + 1, state="verification_pending" if not current else state["state"])
        return GateDecision(False, state["state"], error=message, details=details)

    if changes and not exhausted:
        if not _qa_allowed(workspace, project_id):
            pass  # профиль только чтение: проверки запускать нельзя, отчёт скажет «не проверено»
        elif not current:
            return block("Files changed after the last check. Run qa.run (scope changed) before task.complete; "
                         "it detects and runs the project's lint, type and test checks for the changed files.",
                         changed=list(changes)[:30])
        elif status == "failed" and state["state"] == "repairing":
            failed = [item["id"] for item in qa.get("results", []) if item.get("status") == "failed"]
            return block("Checks failed. Fix the defects and run qa.run again, or if the failure is unrelated to your "
                         "change, explain that in evidence.", failed=failed)
        elif status == "passed" and qa.get("stale"):
            return block("Only some checks were re-run on the current code; " + ", ".join(qa["stale"][:5])
                         + " ran on an older version. Run qa.run without checks to verify the whole set.",
                         stale=qa["stale"][:20])
    if contract.get("acceptance") and not items and not exhausted:
        return block("The task contract has acceptance criteria. Call task.complete with evidence: one item per "
                     "criterion with status met/not_met/unverified and the qa.run or ui.verify check id that proves it.",
                     acceptance=contract["acceptance"])
    judged = judge_evidence(items, qa, current)
    if changes and not current:
        final = "verification_pending"
    elif status == "passed" and qa.get("stale"):
        final = "verification_pending"
    elif status == "passed" and all(item["verdict"] == "verified" for item in judged if item["status"] == "met"):
        final = "verified"
    elif status == "failed":
        final = "failed"
    else:
        final = "unverified"
    if any(item["status"] == "not_met" for item in judged):
        final = "failed" if final != "verification_pending" else final
    review = state["review"] or {}
    if review_enabled and changes and final in {"verified", "unverified"} and not exhausted:
        reviewed = bool(review) and review.get("fingerprint") == (state.get("qa_fingerprint") if current else None)
        if not reviewed and int(state["review_rounds"]) < MAX_REVIEW_ROUNDS:
            return GateDecision(True, final, review_request={"changes": changes, "contract": contract, "qa": qa,
                                                             "fingerprint": state.get("qa_fingerprint") if current else None})
        if reviewed and review.get("blocking") and int(state["review_rounds"]) < MAX_REVIEW_ROUNDS:
            return block("The independent reviewer reported blocking findings. Fix them, run qa.run, then complete "
                         "again (or explain in evidence why a finding is wrong).", findings=review.get("blocking"))
    save(turn_id, state=final, evidence=items)
    return GateDecision(True, final, report=render_report(final, qa, judged, review, contract, state, current))


def apply_review(turn_id: int, fingerprint_value: str | None, verdict: dict[str, Any]) -> GateDecision | None:
    """Сохраняет ревью; блокирующие замечания возвращают отказ, иначе None - можно пересчитать гейт."""
    state = load(turn_id)
    rounds = int(state["review_rounds"]) + (1 if verdict.get("available") else 0)
    blocking = [item for item in verdict.get("findings", []) if item.get("blocking")]
    save(turn_id, review={**verdict, "fingerprint": fingerprint_value if verdict.get("available") else None,
                          "blocking": blocking}, review_rounds=rounds if verdict.get("available") else MAX_REVIEW_ROUNDS)
    if blocking and rounds <= MAX_REVIEW_ROUNDS:
        save(turn_id, gate_blocks=int(state["gate_blocks"]) + 1, state="repairing")
        return GateDecision(False, "repairing", error="The independent reviewer found blocking problems in the diff. Fix "
                            "them (smallest change), run qa.run, then call task.complete again. If a finding is wrong, "
                            "explain why in evidence.", details={"findings": blocking})
    return None


def render_report(final: str, qa: dict, judged: list[dict], review: dict, contract: dict, state: dict, current: bool) -> str:
    lines = [f"**Проверка Докки: {STATE_LABELS.get(final, final)}**"]
    if qa.get("summary") and current:
        lines.append(qa["summary"])
    elif not current:
        lines.append("Автоматические проверки после последних правок не запускались.")
    for note in (qa.get("notes") or [])[:3]:
        lines.append(f"- {note}")
    for item in (qa.get("ui") or [])[-3:]:
        lines.append(f"- UI {item['id']}: {item['status']}")
    marks = {"verified": "✓", "claimed": "~", "not_met": "✕", "unverified": "?"}
    if judged:
        lines.append("Критерии приёмки:")
        for item in judged[:15]:
            suffix = {"claimed": " - со слов агента, без проверки", "unverified": " - не проверено"}.get(item["verdict"], "")
            proof = f" ({item['check']})" if item["verdict"] == "verified" else ""
            lines.append(f"- {marks.get(item['verdict'], '?')} {item['criterion']}{proof}{suffix}")
    elif contract.get("acceptance"):
        lines.append("Критерии приёмки не подтверждены доказательствами.")
    if review.get("available"):
        findings = review.get("findings") or []
        blocking = [item for item in findings if item.get("blocking")]
        stale = review.get("fingerprint") != (state.get("qa_fingerprint") if current else None)
        lines.append(f"Независимое ревью: {len(findings)} замечаний" + (f", блокирующих: {len(blocking)}" if blocking else "")
                     + (" (после последних правок ревью не повторялось)" if stale else ""))
        for item in findings[:5]:
            where = item.get("path", "") + (f":{item['line']}" if item.get("line") else "")
            lines.append(f"- {item.get('severity')}: {where} {item.get('finding', '')}".rstrip())
    elif review:
        lines.append("Независимое ревью недоступно: " + str(review.get("reason") or "нет ответа"))
    if int(state.get("gate_blocks") or 0) >= MAX_GATE_BLOCKS:
        lines.append("Лимит попыток исправления исчерпан, задача завершена без полной проверки.")
    return "\n".join(lines)
