"""quality.run: объективные гейты изменённого кода одним вызовом - сигналы, CRAP, дубли, слои, мутации.

Каждый гейт возвращает GateResult со своим статусом; сбой одного гейта (error) не роняет остальные, отмена хода
помечает оставшиеся как cancelled. Повтор на том же коде, политике и параметрах возвращает сохранённый прогон.
Модель получает выжимку, полный отчёт - по report_id. Решения accepted (принять долг, эквивалентный мутант,
допустимое нарушение слоя) принимает только человек в интерфейсе: агент не может снять собственный провал.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

from koddi.logs import log_event as audit_event
from koddi.quality import budgets
from koddi.quality.contract import GATES, GateResult, compact, find, persist, report, revision, run_key

RUN_ORDER = ("signals", "crap", "dry", "architecture", "mutation")
DECISION_GATES = {"dry", "mutation", "architecture"}
AGENT_DECISIONS = {"review", "refactor"}
MAX_REPORT_PAGE = 50


def _head(root: Path) -> str | None:
    from koddi.tools.runner import HIDDEN_SUBPROCESS

    try:
        proc = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True, timeout=10,
                              check=False, creationflags=HIDDEN_SUBPROCESS)
    except (OSError, subprocess.SubprocessError):
        return None
    return proc.stdout.strip()[:40] if proc.returncode == 0 else None


def overall(results: list[GateResult]) -> str:
    statuses = {item.status for item in results}
    for status in ("error", "failed", "cancelled"):
        if status in statuses:
            return status
    if "passed" in statuses:
        return "passed" if statuses <= {"passed", "skipped"} else "partial"
    return "unavailable" if "unavailable" in statuses else "skipped"


def _graph(project_id: int, root: Path, owner: str, cancel=None):
    from koddi.projectmap import service

    try:
        service.ensure(project_id, root, wait=20.0, cancel=cancel)
        return service.graph(project_id, owner), service.state(project_id).get("version")
    except Exception as exc:  # noqa: BLE001 - без карты гейты по графу честно недоступны
        audit_event("WARNING", "quality.map", str(exc)[:300], {"project_id": project_id})
        return None, None


def _coverage_fingerprint(data) -> list:
    if data is None:
        return []
    return sorted((path, round(item.generated_at)) for path, item in list(data.files.items())[:2000])


def run_gates(runner, *, gates: list[str], paths: list[str], full: bool, budget: dict[str, Any],
              coverage_mode: str, options: dict[str, Any] | None = None, before_texts: dict[str, str | None] | None = None,
              contract: dict[str, Any] | None = None) -> list[GateResult]:
    """Запускает гейты по порядку стоимости и сохраняет каждый результат с ключом идемпотентности."""
    from koddi.policy.account import local_owner
    from koddi.policy.paths import WorkspacePaths
    from koddi.quality import architecture, coverage, metrics, mutation, signals
    from koddi.quality.functions import head_text

    options = dict(options or {})
    root = runner._require_workspace()
    project_id = runner.project_id
    owner = local_owner()
    access = WorkspacePaths(root)

    def accessible(relative: str) -> bool:
        return access.accessible(root / relative)

    paths = [path for path in dict.fromkeys(paths) if accessible(path)]
    source_revision = revision(root, paths)
    head = _head(root)
    before_digest = None if before_texts is None else hashlib.sha256(json.dumps(
        sorted((path, text) for path, text in before_texts.items() if path in paths), ensure_ascii=False).encode()).hexdigest()[:24]
    graph, map_version = (None, None)
    if {"architecture", "signals"} & set(gates):
        graph, map_version = _graph(project_id, root, owner, runner.cancel_event)
    coverage_data = None
    coverage_notes: list[str] = []
    if {"crap", "mutation"} & set(gates) and coverage_mode != "none":
        sources = [path for path in paths if not metrics.is_test_path(path)]
        if coverage_mode == "collect" and sources:
            collected, coverage_notes = coverage.collect(runner, root, sources, timeout=int(budget.get("wall_seconds") or 300) or 300)
            coverage_data = collected
        if coverage_data is None:
            coverage_data = coverage.load(root)
            coverage_notes += coverage_data.notes
    scope = None
    if {"crap", "dry", "mutation"} & set(gates):
        scope = metrics.build_scope(root, paths, before_texts=before_texts, full=full, accessible=accessible)
        scope.notes.extend(coverage_notes)
    results: list[GateResult] = []
    for gate in RUN_ORDER:
        if gate not in gates:
            continue
        key_options = {**options, "gate_budget": {name: budget.get(name) for name in ("max_mutants", "max_files", "coverage")},
                       "coverage_mode": coverage_mode, "full": full, "map": map_version, "paths": sorted(paths),
                       # «изменённое» считается от HEAD или от текста до хода - он тоже часть входа
                       "head": head, "before": before_digest}
        if gate in {"crap", "mutation"}:
            key_options["coverage"] = _coverage_fingerprint(coverage_data)
        if gate == "dry":
            key_options["universe"] = metrics.tree_fingerprint(
                root, accessible=accessible, tests=False, limit=int(budget.get("dry_universe") or metrics.MAX_UNIVERSE_FILES))
        if gate == "mutation":
            key_options["tests"] = metrics.tree_fingerprint(root, accessible=accessible, tests=True)
        if gate == "architecture":
            policy = architecture.get_policy(project_id)
            key_options["policy"] = policy["version"] if policy else None
        key = run_key(gate, source_revision, key_options)
        if runner.cancel_event.is_set():
            results.append(GateResult(gate, "cancelled", paths, source_revision=source_revision))
            continue
        cached = find(project_id, owner, key)
        if cached is not None and cached["status"] in {"passed", "failed", "skipped", "unavailable"}:
            stored = report(cached["id"], owner, project_id=project_id) or {}
            result = GateResult(gate, cached["status"], cached["scope"], source_revision=source_revision,
                                baseline_revision=cached.get("baseline_revision"), findings=stored.get("findings", cached["findings"]),
                                summary={**cached["summary"], "reused": True}, notes=stored.get("notes", []),
                                checker=cached["checker"], error=cached.get("error"), run_id=cached["id"],
                                confidence=cached["summary"].get("confidence") or "exact")
            results.append(result)
            continue
        try:
            if gate == "signals":
                result = signals.signals_gate(root, paths, before_texts=before_texts, full=full, contract=contract, graph=graph)
            elif gate == "crap":
                result = metrics.crap_gate(scope, coverage_data=coverage_data, project_id=project_id, owner=owner,
                                           threshold=float(options.get("crap_threshold") or metrics.CRAP_THRESHOLD),
                                           coverage_mode=coverage_mode)
            elif gate == "dry":
                result = metrics.dry_gate(scope, project_id=project_id, owner=owner,
                                          threshold=float(options.get("dry_threshold") or metrics.DRY_THRESHOLD),
                                          accessible=accessible, full=full,
                                          universe_limit=int(budget.get("dry_universe") or metrics.MAX_UNIVERSE_FILES))
            elif gate == "architecture":
                if graph is None:
                    result = GateResult("architecture", "unavailable", paths, notes=["карта проекта недоступна"])
                else:
                    result = architecture.architecture_gate(
                        graph, root, project_id=project_id, owner=owner, paths=paths, full=full,
                        head_text=lambda path: (before_texts or {}).get(path) if path in (before_texts or {}) else head_text(root, path),
                        read=lambda path: (root / path).read_text(encoding="utf-8", errors="replace")
                        if accessible(path) and (root / path).is_file() else None)
            else:
                result = mutation.mutation_gate(runner, root, scope.changed if scope else [], budget=budget,
                                                coverage_data=coverage_data, accessible=accessible,
                                                project_id=project_id, owner=owner)
        except Exception as exc:  # noqa: BLE001 - сбой одного гейта не должен ронять остальные
            audit_event("ERROR", "quality.gate", f"{gate}: {type(exc).__name__}", {"project_id": project_id})
            result = GateResult(gate, "error", paths, error=f"{type(exc).__name__}: {str(exc)[:300]}")
        result.source_revision = source_revision
        result.baseline_revision = head
        results.append(persist(project_id, owner, runner.turn_id, result, key))
    return results


def _report_page(project_id: int, run_id: int, offset: int, limit: int) -> dict[str, Any]:
    from koddi.policy.account import local_owner

    full = report(run_id, local_owner(), project_id=project_id)
    if full is None:
        return {"ok": False, "error": "Отчёт не найден в этом проекте."}
    findings = full.get("findings") or []
    page = findings[offset:offset + limit]
    return {"ok": True, "report_id": run_id, "gate": full.get("gate"), "status": full.get("status"),
            "summary": full.get("summary"), "notes": full.get("notes", []), "findings": page, "offset": offset,
            "total_findings": len(findings), "more": max(0, len(findings) - offset - len(page))}


def _apply_decisions(project_id: int, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    from koddi.policy.account import local_owner
    from koddi.quality.metrics import set_decision

    applied = []
    for item in items[:30]:
        gate = str(item.get("gate") or "")
        decision = str(item.get("decision") or "")
        key = str(item.get("key") or "")[:600]
        if gate not in DECISION_GATES or not key:
            applied.append({"key": key, "ok": False, "error": "нужны gate (dry/mutation/architecture) и key находки"})
        elif decision not in AGENT_DECISIONS:
            applied.append({"key": key, "ok": False,
                            "error": "принять долг (accepted) может только человек в интерфейсе; агент - review или refactor"})
        else:
            if set_decision(project_id, local_owner(), gate, key, decision, keep_accepted=True):
                applied.append({"key": key, "ok": True, "decision": decision})
            else:
                applied.append({"key": key, "ok": False,
                                "error": "человек уже принял эту находку - агент не меняет его решение"})
    return applied


def quality_tool(runner, args: dict[str, Any]) -> dict[str, Any]:
    from koddi.codeintel.impact import changed_files
    from koddi.policy.account import local_owner
    from koddi.quality import architecture, workflow
    from koddi.quality.gate import load, turn_changes

    root = runner._require_workspace()
    project_id = runner.project_id
    if project_id is None:
        return {"ok": False, "error": "quality.run работает только в папке проекта."}
    if args.get("report_id") is not None:
        offset = max(0, int(args.get("offset") or 0))
        limit = max(1, min(int(args.get("limit") or 20), MAX_REPORT_PAGE))
        return _report_page(project_id, int(args["report_id"]), offset, limit)
    response: dict[str, Any] = {"ok": True}
    if args.get("decisions"):
        response["decisions"] = _apply_decisions(project_id, list(args["decisions"]))
    owner = local_owner()
    if args.get("architecture_proposal") or args.get("suggest_layers"):
        graph, _version = _graph(project_id, root, owner, runner.cancel_event)
        if graph is None:
            return {"ok": False, "error": "Карта проекта недоступна - разметку слоёв не оценить."}

        def read(path):
            target = root / path
            return target.read_text(encoding="utf-8", errors="replace") if target.is_file() else None

        if args.get("suggest_layers"):
            response["suggested_layers"] = {**architecture.suggest_layers(graph), "label": architecture.PROPOSAL_LABEL}
        proposal = args.get("architecture_proposal")
        if isinstance(proposal, dict):
            try:
                saved = architecture.save_proposal(project_id, owner, str(proposal.get("name") or ""),
                                                   {"layers": proposal.get("layers"), "ignore": proposal.get("ignore") or [],
                                                    "allow_type_imports": proposal.get("allow_type_imports", False)},
                                                   str(proposal.get("note") or ""))
            except ValueError as exc:
                return {"ok": False, "error": f"Предложение слоёв отклонено: {exc}"}
            response["proposal"] = {"id": saved["id"], "label": saved["label"],
                                    "evaluation": architecture.evaluate_proposal(graph, saved["policy"], read=read),
                                    "note": "Политика проекта не изменена: применить предложение может только человек."}
            workflow.decision(runner, kind="architecture", summary=f"предложена разметка слоёв «{saved['name']}»",
                              refs={"proposal": saved["id"]})
    gates = [gate for gate in (args.get("gates") or []) if gate in GATES and gate != "tests"]
    if args.get("gates") and not gates:
        # политика разрешила вызов по списку гейтов: подменять пустой список бюджетным (с мутациями) нельзя
        return {**response, "ok": False, "error": "неизвестные гейты: " + ", ".join(map(str, args["gates"]))[:300]}
    if not gates and ("decisions" in response or "proposal" in response or "suggested_layers" in response):
        return response
    depth = getattr(runner, "quality_depth", None)
    budget = budgets.budget_for(depth)
    if not gates:
        gates = list(budget["auto_gates"])
    manual = {}
    if args.get("max_mutants") is not None:
        manual["max_mutants"] = max(0, min(int(args["max_mutants"]), budgets.MANUAL_LIMITS["max_mutants"]))
    if "mutation" in gates and not budget["max_mutants"] and "max_mutants" not in manual:
        manual["max_mutants"] = budgets.BUDGETS["pro"]["max_mutants"]  # явный запрос мутаций в light
    budget = {**budget, **manual}
    if "mutation" in gates and not budget.get("wall_seconds"):
        budget["wall_seconds"] = budgets.BUDGETS["pro"]["wall_seconds"]
        budget["max_files"] = budgets.BUDGETS["pro"]["max_files"]
    budget["wall_seconds"] = min(int(budget.get("wall_seconds") or 0), budgets.MANUAL_LIMITS["wall_seconds"])
    full = args.get("scope") == "full"
    explicit = [str(path).replace("\\", "/").lstrip("/") for path in args.get("paths") or []][:200]
    changes = turn_changes(runner.turn_id)
    paths = explicit or [path for path in changes if (root / path).is_file()] or changed_files(root)
    if full and not explicit:
        from koddi.projectmap.service import walk

        paths = [relative for relative, _absolute in walk(root)][:5000]
    if not paths:
        return {**response, "status": "skipped", "gates": [],
                "notes": ["нет изменённых файлов: укажи paths или scope=full"]}
    # сбор покрытия запускает тесты проекта: в ручном вызове только по явному coverage=collect, который видит
    # политика подтверждений, а не неявно из бюджета max
    coverage_mode = args.get("coverage") or ("existing" if budget["coverage"] == "collect" else budget["coverage"])
    options = {"dry_threshold": args.get("dry_threshold")}
    contract = load(runner.turn_id).get("contract") if runner.turn_id else {}
    results = run_gates(runner, gates=gates, paths=paths, full=full, budget=budget, coverage_mode=coverage_mode,
                        options=options, before_texts=changes or None, contract=contract)
    status = overall(results)
    workflow.record_quality(runner.turn_id, root, list(changes) or paths, results)
    guidance = []
    for item in results:
        if item.status == "failed" and item.gate == "crap":
            guidance.append("CRAP: cover the uncovered branches of the listed functions with behavioural tests or "
                            "simplify conditions; splitting into wrappers only hides complexity.")
        elif item.status == "failed" and item.gate == "mutation":
            guidance.append("Mutation: each survived mutant is a behaviour no test distinguishes. Add a test that fails "
                            "on that change; do not edit tests to match a specific mutant.")
        elif item.status == "failed" and item.gate == "architecture":
            guidance.append("Architecture: an inner layer now imports an outer one. Invert the dependency (interface "
                            "inside, implementation outside) or move the code; a human may accept it in the UI.")
        elif item.status == "failed" and item.gate == "dry":
            guidance.append("DRY: candidates only. Merge only the same business rule that changes together; otherwise "
                            "leave it and mention it.")
        elif item.status == "failed" and item.gate == "signals":
            guidance.append("Signals: review the high-severity lines (disabled tests, swallowed errors, stubs, "
                            "assertion-free tests) and fix them or justify them in the final report.")
        elif item.status == "unavailable":
            guidance.append(f"{item.gate}: unavailable is not passed - report it as not measured.")
    return {**response, "status": status, "ok": status not in {"error"},
            "gates": [compact(item) for item in results],
            "budget": {key: budget.get(key) for key in ("level", "mode", "max_mutants", "wall_seconds")},
            **({"guidance": guidance} if guidance else {})}


def summary_for_review(turn_id: int | None) -> list[dict[str, Any]]:
    """Последние результаты гейтов хода для независимого ревьюера: это измерения, а не мнения."""
    from koddi.quality import workflow

    state = workflow.load(turn_id)
    quality = state.get("quality") or {}
    items = []
    for gate, item in quality.items():
        items.append({"gate": gate, "status": item.get("status"), "summary": item.get("summary"),
                      "top_findings": item.get("top", [])[:5], "current": item.get("fingerprint")})
    return items



# ---------- автоматический прогон перед завершением задачи

GATE_LABELS = {"crap": "CRAP", "dry": "Дубли", "mutation": "Мутации", "architecture": "Слои", "signals": "Сигналы"}
STATUS_LABELS = {"passed": "прошёл", "failed": "провал", "skipped": "пропущен", "unavailable": "не измерен",
                 "cancelled": "отменён", "error": "сбой гейта"}


def automatic(runner, *, depth: str | None, changes: dict[str, str | None], contract: dict[str, Any]) -> tuple[list[GateResult], dict[str, Any]]:
    """Гейты бюджета глубины для изменений хода; пустой список, если пользователь выключил автозапуск."""
    budget = budgets.automatic(depth)
    root = runner._require_workspace()
    paths = [path for path in changes if (root / path).is_file()]
    if budget is None or not paths or runner.project_id is None:
        return [], budget or {}
    from koddi.policy.engine import decide

    gates = list(budget["auto_gates"])
    coverage_mode = budget["coverage"]
    skipped: list[GateResult] = []
    # автозапуск не может спросить человека: тесты проекта идут сами только при полном доступе
    if "mutation" in gates and decide("quality.run", {"gates": ["mutation"]}, root, runner.project_id).action != "allow":
        gates.remove("mutation")
        skipped.append(GateResult("mutation", "skipped", paths, notes=[
            "мутации запускают тесты проекта и требуют подтверждения: агент может вызвать quality.run gates=[mutation]"]))
    if coverage_mode == "collect" and decide("quality.run", {"gates": ["crap"], "coverage": "collect"}, root,
                                             runner.project_id).action != "allow":
        coverage_mode = "existing"
    results = run_gates(runner, gates=gates, paths=paths, full=False, budget=budget,
                        coverage_mode=coverage_mode, before_texts=changes, contract=contract) + skipped
    from koddi.quality import workflow

    workflow.record_quality(runner.turn_id, root, list(changes), results)
    return results, budget


def blocking(results: list[GateResult], budget: dict[str, Any]) -> list[GateResult]:
    return [item for item in results if item.status == "failed" and item.gate in set(budget.get("blocking") or [])]


def report_lines(results: list[GateResult]) -> list[str]:
    """Строки итогового отчёта: статус каждого гейта и главное число без приукрашивания."""
    lines = []
    for item in results:
        summary = item.summary or {}
        detail = ""
        if item.gate == "crap":
            if summary.get("violations"):
                detail = f"функций выше порога {summary.get('threshold')}: {summary['violations']}"
            elif summary.get("unknown_coverage"):
                detail = f"без покрытия: {summary['unknown_coverage']} функций, проверена только сложность"
            elif summary.get("max_crap") is not None:
                detail = f"макс. {summary['max_crap']}"
        elif item.gate == "dry" and summary.get("candidates"):
            detail = f"кандидатов: {summary['candidates']}, открытых: {summary.get('open_candidates', 0)}"
        elif item.gate == "mutation" and summary.get("evaluated"):
            detail = (f"убито {summary.get('killed', 0)} из {summary['evaluated']}, выжило {summary.get('survived', 0)}"
                      + (f", не покрыто {summary['uncovered']}" if summary.get("uncovered") else ""))
        elif item.gate == "architecture" and summary.get("violations"):
            detail = f"нарушений: {summary['violations']}, новых: {summary.get('new', 0)}"
        elif item.gate == "signals" and summary.get("total"):
            detail = f"сигналов: {summary['total']}, важных: {summary.get('high', 0)}"
        if not detail and item.notes:
            detail = item.notes[0]
        lines.append(f"- {GATE_LABELS.get(item.gate, item.gate)}: {STATUS_LABELS.get(item.status, item.status)}"
                     + (f" - {detail}" if detail else ""))
    return lines
