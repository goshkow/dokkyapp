"""Правило зависимостей поверх карты проекта: слои, нарушения, предложения и тепловая карта качества.

Политика задаётся человеком на проект: список слоёв от внутреннего (домен) к внешнему (адаптеры, UI), у каждого -
маски путей. Внутренний слой не должен зависеть от внешнего; внешний от внутреннего - может. Без политики нарушений
нет вообще: Докки не выдумывает архитектуру. Разметку слоёв может предложить агент или эвристика по графу импортов -
это всегда «ПРЕДЛОЖЕНИЕ - не применено»: оно хранится отдельно, оценивается без изменения кода и становится политикой
только действием пользователя.

Строка импорта ищется по требованию (в карте только рёбра файл → файл); импорт только для типов
(`import type`, блок `if TYPE_CHECKING:`) отмечается отдельно - он не тянет код во время выполнения, но связывает слои.
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import PurePosixPath
from typing import Any

from koddi.quality.contract import GateResult, Timer
from koddi.storage.db import db, get_setting, set_setting

POLICY_SETTING = "architecture_policy"
MAX_LAYERS = 12
MAX_GLOBS = 40
MAX_NAME = 40
MAX_GLOB = 200
MAX_PROPOSALS = 30
PROPOSAL_LABEL = "ПРЕДЛОЖЕНИЕ - не применено"


# ---------- политика

def _glob_regex(pattern: str) -> re.Pattern[str]:
    """`**` - любые папки, `*` и `?` - в пределах одного имени; маска без звёздочек - папка или файл целиком."""
    pattern = pattern.strip().strip("/")
    if not any(char in pattern for char in "*?["):
        return re.compile(re.escape(pattern) + r"(?:/.*)?$")
    out = ""
    index = 0
    while index < len(pattern):
        if pattern.startswith("**/", index):
            out += r"(?:.*/)?"
            index += 3
        elif pattern.startswith("**", index):
            out += r".*"
            index += 2
        elif pattern[index] == "*":
            out += r"[^/]*"
            index += 1
        elif pattern[index] == "?":
            out += r"[^/]"
            index += 1
        else:
            out += re.escape(pattern[index])
            index += 1
    return re.compile(out + "$")


def validate_policy(raw: Any) -> dict[str, Any]:
    """Нормализует политику и отклоняет всё, что нельзя однозначно применить."""
    if not isinstance(raw, dict):
        raise ValueError("политика - объект с полем layers")
    layers = raw.get("layers")
    if not isinstance(layers, list) or not 2 <= len(layers) <= MAX_LAYERS:
        raise ValueError(f"нужно от 2 до {MAX_LAYERS} слоёв, от внутреннего к внешнему")
    names: set[str] = set()
    normalized = []
    for layer in layers:
        if not isinstance(layer, dict):
            raise ValueError("слой - объект {name, paths}")
        name = str(layer.get("name") or "").strip()
        if not name or len(name) > MAX_NAME:
            raise ValueError(f"имя слоя - от 1 до {MAX_NAME} символов")
        if name.lower() in names:
            raise ValueError(f"слой {name} указан дважды")
        names.add(name.lower())
        paths = layer.get("paths")
        if not isinstance(paths, list) or not 1 <= len(paths) <= MAX_GLOBS:
            raise ValueError(f"у слоя {name} должно быть от 1 до {MAX_GLOBS} масок путей")
        clean = []
        for item in paths:
            value = str(item or "").strip().replace("\\", "/")
            if not value or len(value) > MAX_GLOB or value.startswith("/") or ".." in PurePosixPath(value).parts \
                    or re.match(r"^[A-Za-z]:", value):
                raise ValueError(f"маска {value!r} в слое {name}: нужен относительный путь внутри проекта")
            clean.append(value.strip("/"))
        normalized.append({"name": name, "paths": list(dict.fromkeys(clean))})
    ignore = raw.get("ignore") or []
    if not isinstance(ignore, list) or len(ignore) > MAX_GLOBS:
        raise ValueError("ignore - список масок")
    ignore = [str(item).strip().strip("/") for item in ignore if str(item).strip()]
    allow_type_imports = bool(raw.get("allow_type_imports", False))
    return {"version": int(raw.get("version") or 1), "layers": normalized, "ignore": ignore,
            "allow_type_imports": allow_type_imports}


def get_policy(project_id: int) -> dict[str, Any] | None:
    stored = (get_setting(POLICY_SETTING, {}) or {}).get(str(project_id))
    if not stored:
        return None
    try:
        return validate_policy(stored)
    except ValueError:
        return None


def set_policy(project_id: int, policy: dict[str, Any] | None) -> dict[str, Any] | None:
    current = dict(get_setting(POLICY_SETTING, {}) or {})
    if policy is None:
        current.pop(str(project_id), None)
        set_setting(POLICY_SETTING, current)
        return None
    normalized = validate_policy(policy)
    previous = current.get(str(project_id)) or {}
    normalized["version"] = int(previous.get("version") or 0) + 1
    current[str(project_id)] = normalized
    set_setting(POLICY_SETTING, current)
    return normalized


class Layers:
    def __init__(self, policy: dict[str, Any]):
        self.names = [layer["name"] for layer in policy["layers"]]
        self.matchers = [[_glob_regex(item) for item in layer["paths"]] for layer in policy["layers"]]
        self.ignore = [_glob_regex(item) for item in policy.get("ignore", [])]
        self.allow_type_imports = bool(policy.get("allow_type_imports"))

    def of(self, path: str) -> int | None:
        if any(item.match(path) for item in self.ignore):
            return None
        # самая конкретная маска выигрывает: вложенная папка может принадлежать другому слою
        best: tuple[int, int] | None = None
        for index, matchers in enumerate(self.matchers):
            for matcher in matchers:
                if matcher.match(path):
                    weight = len(matcher.pattern)
                    if best is None or weight > best[0]:
                        best = (weight, index)
        return best[1] if best else None


# ---------- строки импортов

def _python_import_lines(graph, path: str, text: str, target: str) -> list[tuple[int, bool]]:
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []
    type_only_ranges: list[tuple[int, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.If):
            test = node.test
            name = test.id if isinstance(test, ast.Name) else test.attr if isinstance(test, ast.Attribute) else ""
            if name == "TYPE_CHECKING":
                type_only_ranges.append((node.body[0].lineno, node.body[-1].end_lineno or node.body[-1].lineno))
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            specs = [{"m": alias.name, "lv": 0} for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            specs = [{"m": node.module or "", "lv": node.level, "n": [alias.name for alias in node.names if alias.name != "*"]}]
        else:
            continue
        internal, _ = graph._resolve_imports(path, {"lang": "python", "imports": specs})
        if target in internal:
            type_only = any(start <= node.lineno <= end for start, end in type_only_ranges)
            found.append((node.lineno, type_only))
    return sorted(found)


def _script_import_lines(graph, path: str, text: str, target: str, lang: str) -> list[tuple[int, bool]]:
    from koddi.projectmap.parse import JS_IMPORT

    found = []
    for match in JS_IMPORT.finditer(text):
        spec = match.group(1) or match.group(2)
        if not spec:
            continue
        internal, _ = graph._resolve_imports(path, {"lang": lang, "imports": [spec]})
        if target in internal:
            statement = match.group(0).lstrip("; \t\n")
            type_only = bool(re.match(r"(?:import|export)\s+type\b", statement))
            found.append((text.count("\n", 0, match.start() + (len(match.group(0)) - len(statement))) + 1, type_only))
    return found


def import_lines(graph, path: str, text: str, target: str) -> list[tuple[int, bool]]:
    """(строка, только для типов) для каждого импорта файла path, который ведёт в target."""
    lang = ((graph.files.get(path) or {}).get("info") or {}).get("lang") or ""
    if lang == "python":
        return _python_import_lines(graph, path, text, target)
    if lang in {"typescript", "javascript", "vue", "svelte"}:
        return _script_import_lines(graph, path, text, target, lang)
    return []


# ---------- нарушения

def violations(graph, policy: dict[str, Any], *, sources: list[str] | None = None, read=None) -> dict[str, Any]:
    """Рёбра импорта из внутреннего слоя во внешний. read(path) -> текст файла (для строк), может быть None."""
    layers = Layers(policy)
    assigned: dict[str, int] = {}
    unassigned = 0
    for path in graph.files:
        if graph.is_test(path):
            continue
        layer = layers.of(path)
        if layer is None:
            unassigned += 1
        else:
            assigned[path] = layer
    found: list[dict[str, Any]] = []
    for path in sources if sources is not None else list(assigned):
        source_layer = assigned.get(path)
        if source_layer is None:
            continue
        text = None
        for target in graph.imports.get(path, []):
            target_layer = assigned.get(target)
            if target_layer is None or target_layer <= source_layer:
                continue
            if text is None and read is not None:
                text = read(path) or ""
            lines = import_lines(graph, path, text, target) if text else []
            type_only = bool(lines) and all(item[1] for item in lines)
            if type_only and layers.allow_type_imports:
                continue
            found.append({"key": f"{path}|{target}", "path": path, "target": target,
                          "line_start": lines[0][0] if lines else None, "line_end": lines[0][0] if lines else None,
                          "from_layer": layers.names[source_layer], "to_layer": layers.names[target_layer],
                          "kind": "type_import" if type_only else "import"})
    counts: dict[str, int] = {}
    for item in found:
        edge = f"{item['from_layer']} → {item['to_layer']}"
        counts[edge] = counts.get(edge, 0) + 1
    return {"violations": found, "assigned": len(assigned), "unassigned": unassigned, "edges": counts,
            "layers": layers.names}


def architecture_gate(graph, root, *, project_id: int, owner: str, paths: list[str], full: bool,
                      head_text=None, read=None) -> GateResult:
    """В режиме изменений провал - только новое ребро-нарушение (в HEAD его не было); старые - долг."""
    from koddi.quality.metrics import baseline_rows

    timer = Timer()
    checker = {"id": "dokky-architecture", "version": 1}
    policy = get_policy(project_id)
    if policy is None:
        return GateResult("architecture", "skipped", paths, checker=checker, duration_ms=timer.ms(),
                          notes=["политика слоёв для проекта не задана - нарушений не бывает без правил; "
                                 "предложить разметку можно через quality.run (architecture_proposal)"])
    checker["policy_version"] = policy["version"]
    sources = None if full else [path for path in paths if path in graph.files]
    result = violations(graph, policy, sources=sources, read=read)
    decisions = baseline_rows(project_id, owner, "architecture", [item["key"] for item in result["violations"]])
    findings = []
    new = 0
    for item in result["violations"]:
        decision = decisions.get(item["key"], {}).get("decision", "review")
        existed = None
        if not full and head_text is not None:
            before = head_text(item["path"])
            existed = bool(before) and bool(import_lines(graph, item["path"], before, item["target"]))
        if decision == "accepted":
            kind, severity = "accepted", "info"
        elif existed or (full and existed is None):
            kind, severity = "legacy_violation", "medium"
        else:
            kind, severity = "new_violation", "high"
            new += 1
        findings.append({**item, "metric": "dependency", "value": 1, "severity": severity, "kind": kind,
                         "decision": decision,
                         "evidence": {"from_layer": item["from_layer"], "to_layer": item["to_layer"],
                                      "import": item["kind"], "policy_version": policy["version"]},
                         "advice": f"слой {item['from_layer']} не должен зависеть от {item['to_layer']}: инвертируй "
                                   "зависимость (интерфейс во внутреннем слое, реализация во внешнем) или перенеси код"
                         if kind == "new_violation" else ""})
    open_items = [item for item in findings if item["kind"] != "accepted"]
    status = "failed" if (new if not full else open_items) else "passed"
    notes = []
    if result["unassigned"]:
        notes.append(f"{result['unassigned']} файлов не попали ни в один слой - для них правило не проверяется")
    return GateResult("architecture", status, paths if not full else ["*"], findings=findings, checker=checker,
                      summary={"violations": len(findings), "new": new, "accepted": len(findings) - len(open_items),
                               "assigned_files": result["assigned"], "unassigned_files": result["unassigned"],
                               "edges": result["edges"], "layers": result["layers"]},
                      notes=notes, duration_ms=timer.ms(), confidence="heuristic")


# ---------- предложения

def suggest_layers(graph, *, base: str | None = None, max_layers: int = 4) -> dict[str, Any]:
    """Черновик слоёв по глубине зависимостей папок: папка, которая ничего внутреннего не импортирует, - самая
    внутренняя. Циклы между папками склеиваются в один слой. Это эвристика для обсуждения, не правило."""
    sources = [path for path in graph.files if not graph.is_test(path)
               and ((graph.files[path].get("info") or {}).get("lang") in {"python", "typescript", "javascript"})]
    if not sources:
        return {"layers": [], "note": "нет исходников Python/TypeScript"}
    if base is None:
        parents = [PurePosixPath(path).parent.parts for path in sources]
        common = list(parents[0])
        for parts in parents[1:]:
            while common and list(parts[:len(common)]) != common:
                common.pop()
        base = "/".join(common)
    prefix = base + "/" if base else ""

    def unit(path: str) -> str | None:
        if not path.startswith(prefix):
            return None
        rest = path[len(prefix):].split("/")
        return prefix + rest[0] if len(rest) > 1 else None

    edges: dict[str, set[str]] = {}
    for path in sources:
        owner = unit(path)
        if owner is None:
            continue
        edges.setdefault(owner, set())
        for target in graph.imports.get(path, []):
            other = unit(target)
            if other and other != owner:
                edges[owner].add(other)
                edges.setdefault(other, set())
    if len(edges) < 2:
        return {"layers": [], "note": "в выбранной папке меньше двух модулей - делить не на что"}
    components = _strongly_connected(edges)
    component_of = {node: index for index, group in enumerate(components) for node in group}
    depth: dict[int, int] = {}

    def level(index: int) -> int:
        if index in depth:
            return depth[index]
        depth[index] = 0
        targets = {component_of[target] for node in components[index] for target in edges[node]} - {index}
        depth[index] = 1 + max((level(target) for target in targets), default=-1)
        return depth[index]

    for index in range(len(components)):
        level(index)
    top = max(depth.values())
    buckets: dict[int, list[str]] = {}
    for index, group in enumerate(components):
        bucket = depth[index] if top < max_layers else int(depth[index] * max_layers / (top + 1))
        buckets.setdefault(bucket, []).extend(sorted(group))
    layers = [{"name": f"слой {position + 1}", "paths": [item + "/**" for item in buckets[key]]}
              for position, key in enumerate(sorted(buckets))]
    cycles = [sorted(group) for group in components if len(group) > 1]
    if len(layers) < 2:
        return {"layers": [], "base": base, "cycles": cycles,
                "note": "модули зависят друг от друга по кругу - слои не выделить, сначала стоит разорвать циклы"}
    return {"layers": layers, "base": base, "cycles": cycles,
            "note": "слой 1 - самый внутренний (ничего внутреннего не импортирует); имена и границы - на усмотрение человека"}


def _strongly_connected(edges: dict[str, set[str]]) -> list[list[str]]:
    index_of: dict[str, int] = {}
    low: dict[str, int] = {}
    stack: list[str] = []
    on_stack: set[str] = set()
    result: list[list[str]] = []
    counter = [0]

    def visit(node: str) -> None:
        # итеративный обход Тарьяна: рекурсия упала бы на длинных цепочках модулей
        work = [(node, iter(sorted(edges[node])))]
        index_of[node] = low[node] = counter[0]
        counter[0] += 1
        stack.append(node)
        on_stack.add(node)
        while work:
            current, children = work[-1]
            advanced = False
            for child in children:
                if child not in index_of:
                    index_of[child] = low[child] = counter[0]
                    counter[0] += 1
                    stack.append(child)
                    on_stack.add(child)
                    work.append((child, iter(sorted(edges[child]))))
                    advanced = True
                    break
                if child in on_stack:
                    low[current] = min(low[current], index_of[child])
            if advanced:
                continue
            work.pop()
            if work:
                low[work[-1][0]] = min(low[work[-1][0]], low[current])
            if low[current] == index_of[current]:
                group = []
                while True:
                    item = stack.pop()
                    on_stack.discard(item)
                    group.append(item)
                    if item == current:
                        break
                result.append(group)

    for node in sorted(edges):
        if node not in index_of:
            visit(node)
    return result


def save_proposal(project_id: int, owner: str, name: str, policy: dict[str, Any], note: str = "") -> dict[str, Any]:
    normalized = validate_policy(policy)
    name = (name or "").strip()[:80] or "предложение"
    with db() as conn:
        count = conn.execute("SELECT COUNT(*) FROM architecture_proposals WHERE project_id=? AND owner_id=?",
                             (project_id, owner)).fetchone()[0]
        if count >= MAX_PROPOSALS:
            conn.execute("DELETE FROM architecture_proposals WHERE id IN (SELECT id FROM architecture_proposals "
                         "WHERE project_id=? AND owner_id=? ORDER BY id ASC LIMIT ?)",
                         (project_id, owner, count - MAX_PROPOSALS + 1))
        cursor = conn.execute("INSERT INTO architecture_proposals(project_id,owner_id,name,assignments_json,note) "
                              "VALUES (?,?,?,?,?)", (project_id, owner, name, json.dumps(normalized, ensure_ascii=False),
                                                     (note or "")[:2000]))
        proposal_id = int(cursor.lastrowid)
    return {"id": proposal_id, "name": name, "policy": normalized, "note": note, "label": PROPOSAL_LABEL}


def proposals(project_id: int, owner: str) -> list[dict[str, Any]]:
    with db() as conn:
        rows = conn.execute("SELECT * FROM architecture_proposals WHERE project_id=? AND owner_id=? ORDER BY id DESC",
                            (project_id, owner)).fetchall()
    result = []
    for row in rows:
        try:
            policy = json.loads(row["assignments_json"])
        except ValueError:
            continue
        result.append({"id": row["id"], "name": row["name"], "policy": policy, "note": row["note"],
                       "created_at": row["created_at"], "label": PROPOSAL_LABEL})
    return result


def proposal(project_id: int, owner: str, proposal_id: int) -> dict[str, Any] | None:
    return next((item for item in proposals(project_id, owner) if item["id"] == proposal_id), None)


def delete_proposal(project_id: int, owner: str, proposal_id: int) -> bool:
    with db() as conn:
        return conn.execute("DELETE FROM architecture_proposals WHERE id=? AND project_id=? AND owner_id=?",
                            (proposal_id, project_id, owner)).rowcount > 0


def evaluate_proposal(graph, policy: dict[str, Any], *, read=None) -> dict[str, Any]:
    """Сколько нарушений было бы при этой разметке; код и действующая политика не меняются."""
    result = violations(graph, validate_policy(policy), read=read)
    return {"label": PROPOSAL_LABEL, "violations": len(result["violations"]), "edges": result["edges"],
            "assigned_files": result["assigned"], "unassigned_files": result["unassigned"],
            "sample": result["violations"][:20]}


# ---------- тепловая карта

def heatmap(project_id: int, owner: str) -> dict[str, dict[str, Any]]:
    """Сводка по файлам из снимка метрик и последних прогонов: где копится сложность, дубли и слабые тесты."""
    files: dict[str, dict[str, Any]] = {}

    def slot(path: str) -> dict[str, Any]:
        return files.setdefault(path, {"max_complexity": 0, "max_crap": None, "dry": 0, "survived": 0,
                                       "violations": 0, "score": 0.0})

    with db() as conn:
        rows = conn.execute("SELECT gate,path,metrics_json,decision FROM quality_baseline WHERE project_id=? AND owner_id=?",
                            (project_id, owner)).fetchall()
        runs = conn.execute("SELECT gate,findings_json FROM quality_runs WHERE id IN (SELECT MAX(id) FROM quality_runs "
                            "WHERE project_id=? AND owner_id=? AND status IN ('passed','failed') GROUP BY gate)",
                            (project_id, owner)).fetchall()
    for row in rows:
        if row["gate"] != "crap" or not row["path"]:
            continue
        try:
            metrics = json.loads(row["metrics_json"] or "{}")
        except ValueError:
            continue
        item = slot(row["path"])
        item["max_complexity"] = max(item["max_complexity"], int(metrics.get("complexity") or 0))
        if metrics.get("crap") is not None:
            item["max_crap"] = max(item["max_crap"] or 0, float(metrics["crap"]))
    for run in runs:
        try:
            findings = json.loads(run["findings_json"] or "[]")
        except ValueError:
            continue
        for finding in findings:
            path = finding.get("path")
            if not path or finding.get("decision") == "accepted" or finding.get("kind") == "accepted":
                continue
            item = slot(path)
            if run["gate"] == "dry" and finding.get("decision", "review") == "review":
                item["dry"] += 1
            elif run["gate"] == "mutation" and finding.get("kind") == "survived":
                item["survived"] += 1
            elif run["gate"] == "architecture":
                item["violations"] += 1
    for item in files.values():
        crap = item["max_crap"] if item["max_crap"] is not None else item["max_complexity"]
        # 0..1: CRAP у порога или выше даёт половину шкалы, остальное - дубли, выжившие мутанты, нарушения слоёв
        item["score"] = round(min(1.0, min(crap / 20.0, 0.5) + 0.1 * item["dry"] + 0.1 * item["survived"]
                                  + 0.2 * item["violations"]), 3)
    return {path: item for path, item in files.items() if item["score"] > 0}
