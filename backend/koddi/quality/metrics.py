"""Детерминированные метрики изменённого кода: CRAP (сложность с учётом покрытия) и структурные дубли.

CRAP = CC² × (1 − coverage)³ + CC, coverage - доля от 0 до 1, цель для нового и изменённого кода - строго меньше
порога (10). Без покрытия CRAP неизвестен, но CC - его нижняя граница (при coverage=1 CRAP=CC), поэтому
функция с CC ≥ порога проваливает гейт и без отчёта. Старый долг неизменённых функций показывается
отдельно и не считается новой регрессией; изменённая функция, которая была хуже порога и не ухудшилась,
тоже не провал, а предупреждение.

Дубли - кандидаты на ревью, а не приказ объединять: пары функций одного языка со сходством Жаккара
структурных отпечатков (типы узлов без имён и значений) не ниже порога. Порог по умолчанию 0.82, как
у dryer; маленькие функции, тесты, сгенерированный и сторонний код не сравниваются.
"""

from __future__ import annotations

import bisect
import hashlib
import json
import math
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from koddi.quality.contract import GateResult, Timer
from koddi.quality.functions import Function, changed_lines, eligible, functions_of, head_text, read_source, touched
from koddi.storage.db import db

CRAP_THRESHOLD = 10.0
DRY_THRESHOLD = 0.82
DRY_MIN_LINES = 5
DRY_MIN_NODES = {"python": 30, "script": 45}
SHINGLE = 4
MAX_UNIVERSE_FILES = 4000
DRY_TIME_LIMIT = 60
TEST_HINT = ("tests/", "test/", "__tests__/", "spec/", "/test_", ".test.", ".spec.", "_test.py", "conftest.py", "fixtures/")


def crap_score(complexity: int, coverage: float) -> float:
    if not 0.0 <= coverage <= 1.0:
        raise ValueError("coverage must be a fraction between 0 and 1")
    return complexity * complexity * (1.0 - coverage) ** 3 + complexity


def is_test_path(path: str) -> bool:
    lowered = "/" + path.replace("\\", "/").lower()
    return any(hint in lowered for hint in TEST_HINT)


@dataclass
class Scope:
    """Функции изменённых файлов, отмеченные изменённые и их версии до изменения."""

    root: Path
    paths: list[str]
    functions: dict[str, list[Function]] = field(default_factory=dict)
    changed: list[Function] = field(default_factory=list)
    before: dict[str, list[Function]] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)


def build_scope(root: Path, paths: list[str], *, before_texts: dict[str, str | None] | None = None,
                full: bool = False, accessible=None) -> Scope:
    """before_texts - текст до правок хода; без него сравнение с HEAD. full - все функции считаются изменёнными."""
    scope = Scope(root, [])
    for path in dict.fromkeys(paths):
        if not eligible(path) or (accessible and not accessible(path)):
            continue
        text = read_source(root, path)
        if text is None:
            scope.notes.append(f"{path}: не прочитан")
            continue
        try:
            functions = functions_of(path, text)
            if before_texts is not None and path in before_texts:
                before = before_texts[path]
            else:
                before = head_text(root, path)
            previous = functions_of(path, before) if before else []
            changed = functions if full else touched(functions, changed_lines(before, text))
        except (RecursionError, OSError, ValueError, MemoryError) as exc:
            # один кривой файл не должен ронять гейт по всей области
            scope.notes.append(f"{path}: не разобран ({type(exc).__name__})")
            continue
        scope.paths.append(path)
        scope.functions[path] = functions
        scope.before[path] = previous
        scope.changed.extend(changed)
    return scope


# ---------- снимок долга (baseline)

def baseline_rows(project_id: int, owner: str, gate: str, keys: list[str]) -> dict[str, dict[str, Any]]:
    if not keys:
        return {}
    rows: dict[str, dict[str, Any]] = {}
    with db() as conn:
        for start in range(0, len(keys), 400):
            chunk = keys[start:start + 400]
            for row in conn.execute(
                f"SELECT * FROM quality_baseline WHERE project_id=? AND owner_id=? AND gate=? AND item_key IN ({','.join('?' * len(chunk))})",
                (project_id, owner, gate, *chunk)).fetchall():
                try:
                    metrics = json.loads(row["metrics_json"])
                except ValueError:
                    metrics = {}
                rows[row["item_key"]] = {"body_hash": row["body_hash"], "metrics": metrics, "decision": row["decision"]}
    return rows


def record_baseline(project_id: int, owner: str, gate: str, items: list[tuple[str, str, str, dict[str, Any]]]) -> None:
    """items: (ключ, путь, хеш тела, метрики). Решение пользователя (accepted/refactor) не перезаписывается."""
    if not items:
        return
    with db() as conn:
        conn.executemany(
            "INSERT INTO quality_baseline(project_id,owner_id,gate,item_key,path,body_hash,metrics_json) VALUES (?,?,?,?,?,?,?) "
            "ON CONFLICT(project_id,owner_id,gate,item_key) DO UPDATE SET path=excluded.path,body_hash=excluded.body_hash,"
            "metrics_json=excluded.metrics_json,updated_at=CURRENT_TIMESTAMP",
            [(project_id, owner, gate, key, path, body_hash, json.dumps(metrics, ensure_ascii=False))
             for key, path, body_hash, metrics in items])


def set_decision(project_id: int, owner: str, gate: str, key: str, decision: str, *, keep_accepted: bool = False) -> bool:
    """keep_accepted - решение агента: принятое человеком не перезаписывается (возвращает False)."""
    if decision not in {"review", "accepted", "refactor"}:
        raise ValueError("decision must be review, accepted or refactor")
    guard = " AND decision<>'accepted'" if keep_accepted else ""
    with db() as conn:
        if keep_accepted and conn.execute("SELECT 1 FROM quality_baseline WHERE project_id=? AND owner_id=? AND gate=? "
                                          "AND item_key=? AND decision='accepted'", (project_id, owner, gate, key)).fetchone():
            return False
        cursor = conn.execute("UPDATE quality_baseline SET decision=?,updated_at=CURRENT_TIMESTAMP WHERE project_id=? "
                              "AND owner_id=? AND gate=? AND item_key=?" + guard, (decision, project_id, owner, gate, key))
        if cursor.rowcount == 0 and gate in {"dry", "mutation", "architecture"}:
            conn.execute("INSERT INTO quality_baseline(project_id,owner_id,gate,item_key,path,body_hash,decision) "
                         "VALUES (?,?,?,?,?,?,?)", (project_id, owner, gate, key, key.split("|", 1)[0], "", decision))
            return True
        return cursor.rowcount > 0


# ---------- CRAP

def _function_coverage(function: Function, coverage) -> tuple[float | None, str]:
    if coverage is None:
        return None, "missing"
    # строки def и декораторов выполняются при импорте - считаем только операторы тела
    own = set(function.body_lines) if function.body_lines else set(range(function.line_start, function.line_end + 1))
    statements = {line for line in coverage.statements if line in own}
    if not statements:
        return None, "no_statements"
    executed = statements & coverage.executed
    return len(executed) / len(statements), "ok"


def crap_gate(scope: Scope, *, coverage_data, project_id: int, owner: str, threshold: float = CRAP_THRESHOLD,
              coverage_mode: str = "existing") -> GateResult:
    timer = Timer()
    if not scope.changed:
        return GateResult("crap", "skipped", scope.paths, notes=["нет изменённых функций Python/TypeScript"],
                          duration_ms=timer.ms(), checker={"id": "dokky-crap", "version": 1})
    keys = [item.key for item in scope.changed]
    previous = baseline_rows(project_id, owner, "crap", keys)
    findings: list[dict[str, Any]] = []
    unknown = failures = 0
    record = []
    for function in scope.changed:
        file_coverage, file_state = (None, "disabled") if coverage_mode == "none" or coverage_data is None \
            else coverage_data.lookup(scope.root, function.path)
        fraction, reason = (None, file_state) if file_state != "ok" else _function_coverage(function, file_coverage)
        score = crap_score(function.complexity, fraction) if fraction is not None else None
        lower_bound = float(function.complexity)
        before = next((item for item in scope.before.get(function.path, []) if item.symbol == function.symbol), None)
        renamed = None
        if before is None:
            renamed = next((item for item in scope.before.get(function.path, []) if item.shape_hash == function.shape_hash), None)
        prior = previous.get(function.key, {}).get("metrics", {})
        prior_score = prior.get("crap") if previous.get(function.key, {}).get("body_hash") != function.body_hash else None
        effective = score if score is not None else lower_bound
        evidence = {"complexity": function.complexity, "coverage_fraction": None if fraction is None else round(fraction, 4),
                    "coverage_status": reason, "complexity_before": before.complexity if before else None,
                    "crap_before": prior_score, "language": function.language, "confidence": function.confidence}
        if renamed is not None:
            evidence["identity"] = {"probable_previous": renamed.symbol, "confidence": "heuristic"}
        over = effective >= threshold
        worse = before is not None and (
            (prior_score is not None and score is not None and score > prior_score)
            or function.complexity > before.complexity)
        legacy = before is not None and before.complexity >= threshold and not worse
        if score is None and not over:
            unknown += 1
            status_kind = "unknown"
        elif over and legacy:
            status_kind = "legacy_not_worse"
        elif over:
            failures += 1
            status_kind = "violation"
        else:
            status_kind = "ok"
        if status_kind != "ok":
            findings.append({
                "path": function.path, "line_start": function.line_start, "line_end": function.line_end,
                "symbol": function.symbol, "metric": "crap" if score is not None else "complexity",
                "value": round(effective, 2), "threshold": threshold, "kind": status_kind,
                "severity": {"violation": "high", "legacy_not_worse": "medium", "unknown": "info"}[status_kind],
                "evidence": evidence,
                "advice": "сначала поведенческие тесты на непокрытые ветки или упрощение условий, а не дробление на обёртки"
                if status_kind == "violation" else "",
            })
        record.append((function.key, function.path, function.body_hash,
                       {"complexity": function.complexity, "crap": None if score is None else round(score, 3),
                        "coverage": None if fraction is None else round(fraction, 4), "lines": function.lines}))
    record_baseline(project_id, owner, "crap", record)
    legacy_debt = []
    changed_keys = set(keys)
    for path in scope.paths:
        for function in scope.functions.get(path, []):
            if function.key not in changed_keys and function.complexity >= threshold:
                legacy_debt.append({"path": path, "line_start": function.line_start, "line_end": function.line_end,
                                    "symbol": function.symbol, "metric": "complexity", "value": function.complexity,
                                    "threshold": threshold, "kind": "legacy_debt", "severity": "info",
                                    "evidence": {"complexity": function.complexity, "changed": False}})
    status = "failed" if failures else ("unavailable" if unknown else "passed")
    notes = list(scope.notes)
    if unknown:
        notes.append(f"для {unknown} функций нет пригодного покрытия: CRAP не измерен, проверена только сложность "
                     "(нижняя граница CRAP)")
    if coverage_mode == "none":
        notes.append("режим без покрытия: только сложность, это не полноценная оценка CRAP")
    measured = [item for item in record if item[3]["crap"] is not None]
    summary = {"changed_functions": len(scope.changed), "violations": failures, "unknown_coverage": unknown,
               "legacy_debt": len(legacy_debt), "threshold": threshold,
               "max_crap": max((item[3]["crap"] for item in measured), default=None),
               "max_complexity": max((item.complexity for item in scope.changed), default=None),
               "coverage_reports": getattr(coverage_data, "reports", []) if coverage_data else []}
    return GateResult("crap", status, scope.paths, findings=findings + legacy_debt[:30], summary=summary, notes=notes,
                      duration_ms=timer.ms(), checker={"id": "dokky-crap", "version": 1, "formula": "CC^2*(1-cov)^3+CC"},
                      confidence="heuristic" if any(item.confidence != "exact" for item in scope.changed) else "exact")


# ---------- структурные дубли

def _family(language: str) -> str:
    return "python" if language == "python" else "script"


def shingles(shape: list[str], size: int = SHINGLE) -> set[str]:
    if len(shape) < size:
        return {" ".join(shape)} if shape else set()
    return {hashlib.blake2b(" ".join(shape[index:index + size]).encode(), digest_size=8).hexdigest()
            for index in range(len(shape) - size + 1)}


def similarity(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def _big_enough(function: Function, min_lines: int) -> bool:
    return function.lines >= min_lines and len(function.shape) >= DRY_MIN_NODES[_family(function.language)]


_UNIVERSE_CACHE: dict[tuple[str, str, str], list[Function]] = {}


def universe(root: Path, *, accessible=None, include_tests: bool = False, limit: int = MAX_UNIVERSE_FILES) -> tuple[list[Function], bool]:
    """Функции проекта для сравнения; кэш по пути и хешу файла, обход - как у карты проекта."""
    from koddi.projectmap.service import walk

    found: list[Function] = []
    truncated = False
    count = 0
    # walk уже соблюдает границы доступа, .gitignore и запрет секретных файлов
    for relative, absolute in walk(root):
        if not eligible(relative) or (not include_tests and is_test_path(relative)):
            continue
        if accessible and not accessible(relative):
            continue
        count += 1
        if count > limit:
            truncated = True
            break
        text = read_source(root, relative)
        if text is None:
            continue
        digest = hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()
        # относительный путь в ключе: одна и та же папка, открытая от разных корней, даёт разные пути функций
        key = (str(absolute), relative, digest)
        cached = _UNIVERSE_CACHE.get(key)
        if cached is None:
            cached = functions_of(relative, text)
            if len(_UNIVERSE_CACHE) > 20000:
                _UNIVERSE_CACHE.clear()
            _UNIVERSE_CACHE[key] = cached
        found.extend(cached)
    return found, truncated


def tree_fingerprint(root: Path, *, accessible=None, tests: bool, limit: int = MAX_UNIVERSE_FILES) -> str:
    """Дешёвый отпечаток исходников (tests=False) или тестов (tests=True) по размеру и времени изменения:
    ключ повтора гейта меняется, когда меняется то, с чем он сравнивает, а не только сама область."""
    from koddi.projectmap.service import walk

    digest = hashlib.sha256()
    count = 0
    for relative, absolute in walk(root):
        if not eligible(relative) or is_test_path(relative) != tests:
            continue
        if accessible and not accessible(relative):
            continue
        count += 1
        if count > limit:
            digest.update(b"\0truncated")
            break
        try:
            stat = absolute.stat()
        except OSError:
            continue
        digest.update(f"{relative}:{stat.st_size}:{stat.st_mtime_ns}\0".encode())
    return digest.hexdigest()[:24]


def pair_key(left: Function, right: Function) -> str:
    first, second = sorted([left.key, right.key])
    return f"{first}|{second}"


def dry_gate(scope: Scope, *, project_id: int, owner: str, threshold: float = DRY_THRESHOLD,
             min_lines: int = DRY_MIN_LINES, include_tests: bool = False, accessible=None,
             full: bool = False, universe_limit: int = MAX_UNIVERSE_FILES) -> GateResult:
    timer = Timer()
    if not 0.5 <= threshold <= 1.0:
        raise ValueError("threshold must be between 0.5 and 1.0")
    candidates_source = [item for item in scope.changed if _big_enough(item, min_lines)
                         and (include_tests or not is_test_path(item.path))]
    if not candidates_source and not full:
        return GateResult("dry", "skipped", scope.paths, duration_ms=timer.ms(),
                          notes=[f"нет изменённых функций не короче {min_lines} строк (тесты не сравниваются)"],
                          checker={"id": "dokky-dry", "version": 1})
    pool, truncated = universe(scope.root, accessible=accessible, include_tests=include_tests, limit=universe_limit)
    pool = [item for item in pool if _big_enough(item, min_lines)]
    if full:
        candidates_source = pool
    prints = {item.key: shingles(item.shape) for item in pool}
    for item in candidates_source:
        prints.setdefault(item.key, shingles(item.shape))
    by_family: dict[str, list[tuple[int, Function]]] = {}
    for item in pool:
        by_family.setdefault(_family(item.language), []).append((len(prints[item.key]), item))
    for items in by_family.values():
        items.sort(key=lambda pair: pair[0])
    sizes = {family: [size for size, _ in items] for family, items in by_family.items()}
    seen: set[str] = set()
    pairs = []
    deadline = time.monotonic() + DRY_TIME_LIMIT
    for left in candidates_source:
        if time.monotonic() > deadline:
            truncated_time = True
            break
        left_print = prints[left.key]
        family = _family(left.language)
        # Жаккар не больше отношения размеров: сравниваем только с отпечатками близкого размера
        low = bisect.bisect_left(sizes.get(family, []), math.ceil(len(left_print) * threshold))
        high = bisect.bisect_right(sizes.get(family, []), len(left_print) / threshold)
        for _size, right in by_family.get(family, [])[low:high]:
            if right.key == left.key:
                continue
            key = pair_key(left, right)
            if key in seen:
                continue
            score = similarity(left_print, prints[right.key])
            if score >= threshold:
                seen.add(key)
                pairs.append((score, left, right, key))
    else:
        truncated_time = False
    decisions = baseline_rows(project_id, owner, "dry", [key for *_, key in pairs])
    findings = []
    open_pairs = 0
    for score, left, right, key in sorted(pairs, key=lambda item: -item[0]):
        decision = decisions.get(key, {}).get("decision", "review")
        if decision == "review":
            open_pairs += 1
        findings.append({
            "path": left.path, "line_start": left.line_start, "line_end": left.line_end, "symbol": left.symbol,
            "metric": "structural_similarity", "value": round(score, 3), "threshold": threshold, "key": key,
            "severity": "high" if score >= 0.95 else "medium" if score >= 0.9 else "low",
            "decision": decision,
            "other": {"path": right.path, "line_start": right.line_start, "line_end": right.line_end, "symbol": right.symbol},
            "evidence": {"shared_shingles": len(prints[left.key] & prints[right.key]), "nodes": [len(left.shape), len(right.shape)],
                         "language": left.language},
            "advice": "кандидат: объединять, только если это одно и то же бизнес-правило, меняется вместе и общая "
                      "абстракция не связывает разные домены; иначе отметить accepted с причиной",
        })
    if findings:
        record_baseline(project_id, owner, "dry", [
            (item["key"], item["path"], "", {"score": item["value"], "other": item["other"]["path"]}) for item in findings])
    notes = list(scope.notes)
    if truncated:
        notes.append(f"проект больше {universe_limit} файлов: сравнение шло с первой частью")
    if truncated_time:
        notes.append(f"сравнение остановлено по лимиту {DRY_TIME_LIMIT} с - найдены не все кандидаты")
    # неполное сравнение без находок - не доказательство отсутствия дублей
    status = "failed" if open_pairs else ("unavailable" if truncated or truncated_time else "passed")
    summary = {"compared_functions": len(candidates_source), "pool": len(pool), "candidates": len(findings),
               "open_candidates": open_pairs, "threshold": threshold, "min_lines": min_lines,
               "truncated": bool(truncated or truncated_time)}
    return GateResult("dry", status, scope.paths, findings=findings[:100], summary=summary, notes=notes,
                      duration_ms=timer.ms(), confidence="heuristic",
                      checker={"id": "dokky-dry", "version": 1, "shingle": SHINGLE})
