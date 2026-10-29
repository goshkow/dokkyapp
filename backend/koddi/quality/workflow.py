"""Инженерный цикл хода: стадии Make It Work → Make It Right → Practice, red/green, атака на предпосылку,
цепочка причин, проверяемые шаги и журнал решений агента.

Всё хранится в turn_quality.workflow_json и agent_decisions, а не в памяти процесса: после перезапуска или
сжатия контекста агент получает выжимку в снимке контекста хода (context_hint) и продолжает с того же места.

Red засчитывается, только если тест упал по нужной причине: ошибка импорта, синтаксиса, сборки или отсутствующая
фикстура - это не красный тест, а сломанный запуск. Green - тот же check после правки прошёл.
Атака на предпосылку: одна и та же проверка упала на двух разных версиях кода - значит, исправления строятся
на неверном допущении; следующий прогон требует явно назвать допущение, опровергающие факты и новый подход.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from koddi.storage.db import db

STAGES = ("make_it_work", "make_it_right", "practice")
DECISION_KINDS = ("approach", "tradeoff", "scope", "rollback", "premise", "root_cause", "architecture", "quality", "other")
MAX_DECISIONS_IN_HINT = 6
PREMISE_AFTER = 2
WRONG_REASON = re.compile(
    r"ModuleNotFoundError|ImportError|NameError|SyntaxError|IndentationError|fixture '[^']+' not found|"
    r"ERROR collecting|errors? during collection|Cannot find module|Failed to (?:load|resolve)|Transform failed|"
    r"is not defined|ReferenceError|error TS\d+|No module named|command not found", re.I)
RIGHT_REASON = re.compile(
    r"AssertionError|\bassert\b|Expected|expect\(|toBe|toEqual|toMatch|toThrow|toHaveBeenCalled|AssertError|"
    r"assert_called|pytest\.raises|DID NOT RAISE|!= |== ", re.I)


LIST_LIMITS = {"stages": 20, "green": 30, "premises": 10, "red": 10, "units": 30}


def load(turn_id: int | None) -> dict[str, Any]:
    if turn_id is None:
        return {}
    with db() as conn:
        row = conn.execute("SELECT workflow_json FROM turn_quality WHERE turn_id=?", (turn_id,)).fetchone()
    if row is None:
        return {}
    try:
        value = json.loads(row["workflow_json"] or "{}")
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def save(turn_id: int | None, value: dict[str, Any]) -> None:
    from koddi.quality.gate import save as save_quality

    if turn_id is None:
        return
    # обрезка строки JSON сломала бы его целиком и молча стёрла бы стадию и red/green: режем списки
    for key, keep in LIST_LIMITS.items():
        if isinstance(value.get(key), list):
            value[key] = value[key][-keep:]
    if isinstance(value.get("failures"), dict):
        value["failures"] = dict(list(value["failures"].items())[-40:])
    # turn_quality создаётся тем же upsert, что и остальное состояние гейта
    save_quality(turn_id, workflow_json=json.dumps(value, ensure_ascii=False))


def _chat_of(turn_id: int | None) -> int | None:
    if turn_id is None:
        return None
    with db() as conn:
        row = conn.execute("SELECT chat_id FROM turns WHERE id=?", (turn_id,)).fetchone()
    return int(row["chat_id"]) if row else None


def log_decision(turn_id: int | None, project_id: int | None, *, kind: str, summary: str,
                 refs: dict[str, Any] | None = None) -> int | None:
    chat_id = _chat_of(turn_id)
    if chat_id is None or not summary.strip():
        return None
    kind = kind if kind in DECISION_KINDS else "other"
    with db() as conn:
        cursor = conn.execute("INSERT INTO agent_decisions(chat_id,turn_id,project_id,kind,summary,refs_json) VALUES (?,?,?,?,?,?)",
                              (chat_id, turn_id, project_id, kind, summary.strip()[:1000],
                               json.dumps(refs or {}, ensure_ascii=False)[:4000]))
        return int(cursor.lastrowid)


def decision(runner, *, kind: str, summary: str, refs: dict[str, Any] | None = None) -> int | None:
    return log_decision(runner.turn_id, runner.project_id, kind=kind, summary=summary, refs=refs)


def decisions(chat_id: int, *, limit: int = 50) -> list[dict[str, Any]]:
    with db() as conn:
        rows = conn.execute("SELECT * FROM agent_decisions WHERE chat_id=? ORDER BY id DESC LIMIT ?",
                            (chat_id, max(1, min(limit, 500)))).fetchall()
    result = []
    for row in rows:
        try:
            refs = json.loads(row["refs_json"] or "{}")
        except ValueError:
            refs = {}
        result.append({"id": row["id"], "turn_id": row["turn_id"], "kind": row["kind"], "summary": row["summary"],
                       "refs": refs, "created_at": row["created_at"]})
    return result


# ---------- plan.update

def _text(value: Any, limit: int = 500) -> str:
    return str(value or "").strip()[:limit]


def _texts(value: Any, *, items: int = 12, limit: int = 400) -> list[str]:
    if not isinstance(value, list):
        return []
    return [text for item in value[:items] if (text := _text(item, limit))]


def apply_plan_update(turn_id: int | None, project_id: int | None, args: dict[str, Any], *, qa: dict[str, Any] | None = None,
                      current: bool = False) -> dict[str, Any]:
    """Сохраняет стадию, проверки шагов, цепочку причин, атаку на предпосылку и решение; возвращает замечания."""
    state = load(turn_id)
    notes: list[str] = []
    changed = False
    passed = {item["id"] for item in (qa or {}).get("results", []) if item.get("status") == "passed"} if current else set()
    steps = args.get("steps") if isinstance(args.get("steps"), list) else []
    units = []
    for step in steps:
        if isinstance(step, dict) and step.get("check"):
            unit = {"title": _text(step.get("title"), 200), "check": _text(step.get("check"), 200), "status": step.get("status")}
            unit["verified"] = unit["check"] in passed or unit["check"] in set((qa or {}).get("ui_passed") or [])
            if unit["status"] == "completed" and not unit["verified"]:
                notes.append(f"шаг «{unit['title']}» отмечен выполненным, но его проверка {unit['check']} на текущем коде не прошла")
            units.append(unit)
    if units:
        state["units"] = units
        changed = True
    stage = args.get("stage")
    if stage in STAGES and stage != state.get("stage"):
        previous = state.get("stage") or "make_it_work"
        if STAGES.index(stage) > STAGES.index(previous) and stage != "make_it_work" and not passed:
            notes.append("стадия сменилась без прошедших проверок на текущем коде: сначала Make It Work доказывается тестом")
        state["stage"] = stage
        state.setdefault("stages", []).append({"stage": stage, "verified_checks": sorted(passed)[:10]})
        changed = True
    root_cause = args.get("root_cause")
    if isinstance(root_cause, dict) and _text(root_cause.get("cause")):
        state["root_cause"] = {"symptom": _text(root_cause.get("symptom")), "chain": _texts(root_cause.get("chain")),
                               "cause": _text(root_cause.get("cause")), "evidence": _texts(root_cause.get("evidence"))}
        if len(state["root_cause"]["chain"]) < 2:
            notes.append("цепочка причин короче двух звеньев: проверь, что это причина, а не симптом")
        log_decision(turn_id, project_id, kind="root_cause", summary=state["root_cause"]["cause"],
                     refs={"symptom": state["root_cause"]["symptom"]})
        changed = True
    premise = args.get("premise")
    if isinstance(premise, dict) and _text(premise.get("assumption")) and _text(premise.get("new_approach")):
        record = {"assumption": _text(premise.get("assumption")), "refuting_facts": _texts(premise.get("refuting_facts")),
                  "new_approach": _text(premise.get("new_approach")), "check": state.get("premise_required")}
        if not record["refuting_facts"]:
            notes.append("атака на предпосылку без опровергающих фактов не снимает требование: назови, что именно ей противоречит")
        else:
            state.setdefault("premises", []).append(record)
            state.pop("premise_required", None)
            log_decision(turn_id, project_id, kind="premise", summary=f"{record['assumption']} → {record['new_approach']}",
                         refs={"check": record["check"]})
        changed = True
    entry = args.get("decision")
    if isinstance(entry, dict) and _text(entry.get("summary")):
        log_decision(turn_id, project_id, kind=_text(entry.get("kind"), 40), summary=_text(entry.get("summary"), 1000),
                     refs={"refs": _texts(entry.get("refs"), items=10, limit=300)})
    if changed:
        save(turn_id, state)
    return {"workflow": summary(state), **({"workflow_notes": notes} if notes else {})}


# ---------- red / green и предпосылка

def red_verdict(results: list[dict[str, Any]], reason: str | None) -> dict[str, Any]:
    """Упал ли тест по правильной причине: assertion, а не сломанный запуск."""
    tests = [item for item in results if item.get("kind") == "test"]
    if not tests:
        return {"verified": False, "why": "среди проверок нет тестов - red доказывается падающим тестом"}
    failed = [item for item in tests if item.get("status") == "failed"]
    if not failed:
        statuses = ", ".join(sorted({item.get("status", "?") for item in tests}))
        return {"verified": False, "why": f"тест не упал (статус: {statuses}) - он не воспроизводит проблему"}
    output = "\n".join(item.get("output_tail") or "" for item in failed)
    if WRONG_REASON.search(output):
        match = WRONG_REASON.search(output).group(0)
        return {"verified": False, "why": f"падение из-за запуска, а не поведения ({match}): почини импорт/сборку и повтори"}
    if not RIGHT_REASON.search(output):
        return {"verified": False, "why": "в выводе нет проверки (assert/expect) - причина падения не доказана"}
    matched = None
    if reason:
        words = [word for word in re.findall(r"\w{4,}", reason.lower())]
        quoted = re.findall(r"[\"'`]([^\"'`]{3,})[\"'`]", reason)
        lowered = output.lower()
        if quoted:
            matched = all(item.lower() in lowered for item in quoted)
        elif words:
            matched = sum(word in lowered for word in words) / len(words) >= 0.5
        if matched is False:
            return {"verified": False, "reason_matched": False,
                    "why": "тест упал, но вывод не похож на ожидаемую причину - возможно, он ловит другое"}
    tests_failed = sorted({diag.get("test") for item in failed for diag in item.get("diagnostics", []) if diag.get("test")})[:10]
    return {"verified": True, "reason_matched": matched, "checks": [item["id"] for item in failed], "tests": tests_failed}


def record_red(turn_id: int | None, project_id: int | None, fingerprint: str, verdict: dict[str, Any], reason: str | None) -> None:
    if turn_id is None:
        return
    state = load(turn_id)
    entry = {"fingerprint": fingerprint, "verified": verdict["verified"], "reason": (reason or "")[:300],
             "checks": verdict.get("checks", []), "tests": verdict.get("tests", []), "why": verdict.get("why")}
    state["red"] = [*state.get("red", [])[-9:], entry]
    save(turn_id, state)
    if verdict["verified"]:
        log_decision(turn_id, project_id, kind="quality", summary="red подтверждён: тест падает по нужной причине",
                     refs={"checks": entry["checks"], "tests": entry["tests"]})


def record_checks(turn_id: int | None, fingerprint: str, results: list[dict[str, Any]]) -> dict[str, Any]:
    """Green для ранее красных проверок и счётчик падений одной проверки на разных версиях кода."""
    if turn_id is None:
        return {}
    state = load(turn_id)
    response: dict[str, Any] = {}
    reds = {check for entry in state.get("red", []) if entry.get("verified") for check in entry.get("checks", [])}
    greens = state.setdefault("green", [])
    for item in results:
        if item.get("status") == "passed" and item["id"] in reds and item["id"] not in {entry["check"] for entry in greens}:
            greens.append({"check": item["id"], "fingerprint": fingerprint})
            response.setdefault("red_green", []).append(item["id"])
    failures = state.setdefault("failures", {})
    for item in results:
        if item.get("status") != "failed":
            continue
        prints = failures.setdefault(item["id"], [])
        if fingerprint not in prints:
            prints.append(fingerprint)
            del prints[:-6]
        resolved = {entry.get("check") for entry in state.get("premises", [])}
        if len(prints) >= PREMISE_AFTER and item["id"] not in resolved and not state.get("premise_required"):
            state["premise_required"] = item["id"]
    for item in results:
        if item.get("status") == "passed" and item["id"] in failures:
            failures.pop(item["id"], None)
            if state.get("premise_required") == item["id"]:
                state.pop("premise_required", None)
    if state.get("premise_required"):
        response["premise_required"] = state["premise_required"]
    save(turn_id, state)
    return response


def premise_block(turn_id: int | None, checks: list[str] | None) -> str | None:
    """Ошибка для qa.run, пока не названа неверная предпосылка после повторных падений."""
    required = load(turn_id).get("premise_required")
    if not required or (checks and required not in checks):
        return None
    return (f"Check {required} failed again on a different version of the code: the fixes rest on a wrong assumption. "
            "Before running it again call plan.update with premise {assumption: what you assumed, refuting_facts: "
            "what contradicts it, new_approach: what you will do instead}. Re-read the failing output and the code path "
            "first; do not retry the same idea.")


# ---------- объективные гейты

def record_quality(turn_id: int | None, root: Path, paths: list[str], results) -> None:
    from koddi.quality.gate import fingerprint

    if turn_id is None:
        return
    state = load(turn_id)
    current = fingerprint(root, paths)
    quality = state.setdefault("quality", {})
    for item in results:
        quality[item.gate] = {
            "status": item.status, "run_id": item.run_id, "fingerprint": current,
            "summary": {key: value for key, value in (item.summary or {}).items()
                        if isinstance(value, (int, float, str, bool)) or value is None},
            "top": [{key: finding.get(key) for key in ("path", "line_start", "symbol", "kind", "severity", "value", "key")}
                    for finding in item.findings if finding.get("severity") in {"high", "medium"}][:8],
        }
    save(turn_id, state)


def summary(state: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {"stage": state.get("stage") or "make_it_work"}
    if state.get("units"):
        result["units"] = [{"title": item["title"], "check": item["check"], "verified": item["verified"]}
                           for item in state["units"][:12]]
    reds = state.get("red") or []
    if reds:
        result["red"] = {"verified": reds[-1]["verified"], "checks": reds[-1].get("checks", []), "why": reds[-1].get("why")}
    if state.get("green"):
        result["green"] = [item["check"] for item in state["green"][-6:]]
    if state.get("premise_required"):
        result["premise_required"] = state["premise_required"]
    if state.get("root_cause"):
        result["root_cause"] = state["root_cause"]["cause"]
    if state.get("quality"):
        result["quality"] = {gate: item["status"] for gate, item in state["quality"].items()}
    return result


def latest(chat_id: int) -> tuple[int | None, dict[str, Any]]:
    """Последний ход чата с инженерным состоянием и само состояние."""
    with db() as conn:
        row = conn.execute("SELECT q.turn_id,q.workflow_json FROM turn_quality q JOIN turns t ON t.id=q.turn_id "
                           "WHERE t.chat_id=? AND q.workflow_json<>'{}' ORDER BY q.turn_id DESC LIMIT 1", (chat_id,)).fetchone()
    if row is None:
        return None, {}
    try:
        state = json.loads(row["workflow_json"] or "{}")
    except ValueError:
        state = {}
    return int(row["turn_id"]), state if isinstance(state, dict) else {}


def context_hint(chat_id: int) -> dict[str, Any] | None:
    """Выжимка последнего инженерного состояния чата для снимка контекста: переживает сжатие истории."""
    turn_id, state = latest(chat_id)
    recent = decisions(chat_id, limit=MAX_DECISIONS_IN_HINT)
    if not state and not recent:
        return None
    hint = {"previous_turn": turn_id, **summary(state)} if state else {}
    if recent:
        hint["decisions"] = [f"{item['kind']}: {item['summary'][:200]}" for item in reversed(recent)]
    hint["note"] = "Engineering state from earlier turns of this chat (survives context compaction). Continue from it."
    return hint
