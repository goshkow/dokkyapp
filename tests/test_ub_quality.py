"""UB-режим: объективные гейты (CRAP, дубли, мутации, слои), red/green, атака на предпосылку, журнал решений,
безопасный git и один писатель на папку."""

from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

import koddi.storage.db as storage
from koddi.projectmap.graph import Graph
from koddi.projectmap.parse import parse_file
from koddi.quality import architecture, budgets, checks, metrics, mutation, signals, workflow
from koddi.quality.contract import GateResult
from koddi.quality.coverage import CoverageData, FileCoverage
from koddi.quality.functions import functions_of

OWNER = "__test__"


def write(root: Path, relative: str, text: str) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def git(root: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, check=True).stdout


def git_repo(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.email", "t@example.com")
    git(root, "config", "user.name", "t")
    git(root, "config", "commit.gpgsign", "false")
    return root


def graph_of(texts: dict[str, str]) -> Graph:
    return Graph({path: {"info": parse_file(path, text.encode()), "sha256": str(hash(text)), "size": len(text)}
                  for path, text in texts.items()}, [])


@pytest.fixture
def env(tmp_path, monkeypatch):
    from koddi.projects import add_project
    from koddi.tools.runner import ToolRunner

    monkeypatch.setattr(storage, "DB_PATH", tmp_path / "ub.sqlite3")
    monkeypatch.setenv("KODDI_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr("koddi.policy.grants.permission_profile", lambda project_id=None, **_: "full")
    monkeypatch.setattr("koddi.policy.account.local_owner", lambda: OWNER)
    storage.init_db()
    root = git_repo(tmp_path / "calc")
    write(root, "calc.py", "def add(a, b):\n    return a + b\n")
    project = add_project(str(root))
    with storage.db() as conn:
        chat = int(conn.execute("INSERT INTO chats(title,project_id,mode,owner_id) VALUES ('t',?,'development',?)",
                                (project["id"], OWNER)).lastrowid)

    def turn() -> int:
        with storage.db() as conn:
            return int(conn.execute("INSERT INTO turns(chat_id,status) VALUES (?,'running')", (chat,)).lastrowid)

    first = turn()
    yield SimpleNamespace(root=root.resolve(), project_id=project["id"], chat_id=chat, turn_id=first, turn=turn,
                          runner=ToolRunner(root.resolve(), project["id"], first))
    from koddi.projectmap import service

    service.shutdown()


# ---------- CRAP

BRANCHY = """def grade(score, bonus):
    if score > 90:
        return "A"
    if score > 80 and bonus:
        return "A-"
    if score > 70:
        return "B"
    if score > 60 or bonus:
        return "C"
    return "D"
"""


def test_crap_formula_examples_and_boundary():
    assert metrics.crap_score(4, 0.0) == 20.0  # 4² × 1 + 4
    assert metrics.crap_score(4, 1.0) == 4.0  # полное покрытие - только сложность
    assert metrics.crap_score(3, 0.5) == pytest.approx(9 * 0.125 + 3)
    with pytest.raises(ValueError):
        metrics.crap_score(2, 1.5)


def _coverage(root: Path, path: str, executed: set[int], statements: set[int]) -> CoverageData:
    return CoverageData(files={path: FileCoverage(statements, executed, "test", time.time() + 5)}, reports=["test"])


def test_crap_gate_fails_on_uncovered_complex_function_and_passes_when_covered(env):
    write(env.root, "grade.py", BRANCHY)
    scope = metrics.build_scope(env.root, ["grade.py"], before_texts={"grade.py": None})
    assert [item.symbol for item in scope.changed] == ["grade"]
    lines = set(range(1, 11))
    bare = metrics.crap_gate(scope, coverage_data=_coverage(env.root, "grade.py", {1}, lines),
                             project_id=env.project_id, owner=OWNER)
    assert bare.status == "failed" and bare.summary["violations"] == 1
    assert bare.findings[0]["kind"] == "violation" and bare.findings[0]["value"] >= 10
    covered = metrics.crap_gate(scope, coverage_data=_coverage(env.root, "grade.py", lines, lines),
                                project_id=env.project_id, owner=OWNER)
    assert covered.status == "passed" and covered.summary["max_crap"] < 10


def test_crap_without_coverage_is_unavailable_not_passed(env):
    write(env.root, "small.py", "def pick(a, b):\n    if a:\n        return a\n    return b\n")
    scope = metrics.build_scope(env.root, ["small.py"], before_texts={"small.py": None})
    result = metrics.crap_gate(scope, coverage_data=CoverageData(), project_id=env.project_id, owner=OWNER)
    assert result.status == "unavailable"
    assert result.summary["unknown_coverage"] == 1
    assert any("не измерен" in note for note in result.notes)
    # сложность выше порога валит гейт и без покрытия: нижняя граница CRAP уже не меньше порога
    write(env.root, "huge.py", "def f(x):\n" + "".join(f"    if x == {n}:\n        return {n}\n" for n in range(11)) + "    return -1\n")
    huge = metrics.build_scope(env.root, ["huge.py"], before_texts={"huge.py": None})
    assert metrics.crap_gate(huge, coverage_data=None, project_id=env.project_id, owner=OWNER).status == "failed"


# ---------- дубли

def test_dry_finds_structural_duplicate_with_renamed_locals_and_human_decision(env):
    first = ("def total_price(items, tax):\n    result = 0\n    for item in items:\n        if item.active:\n"
             "            result += item.price * item.count\n    result = result * (1 + tax)\n    return round(result, 2)\n")
    second = first.replace("total_price", "order_sum").replace("result", "acc").replace("item", "row").replace("tax", "vat")
    write(env.root, "a.py", first)
    git(env.root, "add", "a.py")
    git(env.root, "commit", "-qm", "a")
    write(env.root, "b.py", second)
    scope = metrics.build_scope(env.root, ["b.py"], before_texts={"b.py": None})
    result = metrics.dry_gate(scope, project_id=env.project_id, owner=OWNER, min_lines=4)
    assert result.status == "failed", result.notes
    finding = result.findings[0]
    assert finding["other"]["path"] == "a.py" and finding["value"] >= metrics.DRY_THRESHOLD
    assert "кандидат" in finding["advice"]
    # человек признал дубль допустимым - гейт его больше не блокирует
    assert metrics.set_decision(env.project_id, OWNER, "dry", finding["key"], "accepted")
    again = metrics.dry_gate(scope, project_id=env.project_id, owner=OWNER, min_lines=4)
    assert again.status == "passed"


def test_dry_ignores_unrelated_functions(env):
    write(env.root, "a.py", "def parse(text):\n    parts = text.split(',')\n    out = []\n    for part in parts:\n"
                            "        out.append(part.strip())\n    return out\n")
    git(env.root, "add", "a.py")
    git(env.root, "commit", "-qm", "a")
    write(env.root, "b.py", "class Box:\n    def area(self, w, h):\n        if w < 0 or h < 0:\n            raise ValueError(w)\n"
                            "        value = w * h\n        if value > 100:\n            value = 100\n        return value\n")
    scope = metrics.build_scope(env.root, ["b.py"], before_texts={"b.py": None})
    assert metrics.dry_gate(scope, project_id=env.project_id, owner=OWNER, min_lines=4).status == "passed"


# ---------- мутации

CALC = """def grade(score):
    if score >= 90:
        return "A"
    if score > 50:
        return "B"
    return "C"
"""
WEAK = 'from calc import grade\n\n\ndef test_a():\n    assert grade(95) == "A"\n'
STRONG = WEAK + ('\n\ndef test_edges():\n    assert grade(90) == "A"\n    assert grade(89) == "B"\n'
                 '    assert grade(51) == "B"\n    assert grade(50) == "C"\n')


@pytest.fixture
def mutation_project(env, monkeypatch):
    write(env.root, "calc.py", CALC)
    write(env.root, "conftest.py", "")
    write(env.root, "tests/test_calc.py", WEAK)
    check = checks.Check("python:test:pytest@", "test", "pytest", "",
                         [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests/test_calc.py"],
                         targets=["tests/test_calc.py"])
    monkeypatch.setattr(checks, "detect", lambda root, files, scope, project_id: ([check], []))
    return SimpleNamespace(env=env, functions=functions_of("calc.py", CALC))


def _mutate(project, **budget) -> GateResult:
    env = project.env
    return mutation.mutation_gate(env.runner, env.root, project.functions,
                                  budget={"max_mutants": 12, "wall_seconds": 120, **budget},
                                  project_id=env.project_id, owner=OWNER)


def test_mutation_weak_tests_leave_survivors_strong_tests_kill_them(mutation_project):
    before = (mutation_project.env.root / "calc.py").read_bytes()
    weak = _mutate(mutation_project)
    assert weak.status == "failed" and weak.summary["survived"] > 0
    assert all(item["kind"] != "killed" for item in weak.findings)
    survivor = next(item for item in weak.findings if item["kind"] == "survived")
    assert survivor["evidence"]["original"] != survivor["evidence"]["replacement"]
    write(mutation_project.env.root, "tests/test_calc.py", STRONG)
    strong = _mutate(mutation_project)
    assert strong.summary["killed"] > weak.summary["killed"]
    assert strong.summary["survived"] < weak.summary["survived"]
    # файлы проекта не трогаются, временные копии убраны
    assert (mutation_project.env.root / "calc.py").read_bytes() == before
    assert not [item for item in mutation.overlays_root().glob("mut-*")]


def test_mutation_requires_green_baseline(mutation_project):
    write(mutation_project.env.root, "tests/test_calc.py", WEAK + "\n\ndef test_broken():\n    assert False\n")
    result = _mutate(mutation_project)
    assert result.status == "unavailable" and result.error == "baseline_failed"
    assert result.summary["killed"] == 0


def test_mutation_cancel_stops_and_leaves_project_untouched(mutation_project):
    env = mutation_project.env
    before = (env.root / "calc.py").read_bytes()
    env.runner.cancel_event.set()
    try:
        result = _mutate(mutation_project)
    finally:
        env.runner.cancel_event.clear()
    assert result.status == "cancelled"
    assert (env.root / "calc.py").read_bytes() == before
    assert not [item for item in mutation.overlays_root().glob("mut-*")]


def test_mutation_outside_budget_is_skipped(mutation_project):
    assert _mutate(mutation_project, max_mutants=0).status == "skipped"


def test_python_mutants_compile_and_stay_inside_changed_functions():
    text = "X = 1\n\n" + CALC
    functions = functions_of("calc.py", text)
    mutants = mutation.mutants_for("calc.py", text, functions)
    assert mutants
    operators = {item.operator for item in mutants}
    assert len(operators) >= 2, operators
    for item in mutants:
        mutated = item.apply(text)
        assert mutated != text
        compile(mutated, "calc.py", "exec")
        # строка вне изменённой функции (X = 1) не мутируется
        assert item.line >= 3 and item.symbol == "grade"


# ---------- слои архитектуры

LAYERED = {
    "app/__init__.py": "", "app/domain/__init__.py": "", "app/infra/__init__.py": "", "app/api/__init__.py": "",
    "app/domain/order.py": "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    from app.api import routes\n"
                           "from app.infra import db\n",
    "app/infra/db.py": "from app.domain import order\n",
    "app/api/routes.py": "from app.domain import order\nfrom app.infra import db\n",
}
POLICY = {"layers": [{"name": "domain", "paths": ["app/domain/**"]}, {"name": "infra", "paths": ["app/infra/**"]},
                     {"name": "api", "paths": ["app/api/**"]}]}


def test_policy_validation_rejects_ambiguous_or_escaping_layers():
    with pytest.raises(ValueError):
        architecture.validate_policy({"layers": [{"name": "only", "paths": ["a/**"]}]})
    with pytest.raises(ValueError):
        architecture.validate_policy({"layers": [{"name": "x", "paths": ["../etc"]}, {"name": "y", "paths": ["a"]}]})
    with pytest.raises(ValueError):
        architecture.validate_policy({"layers": [{"name": "x", "paths": ["a"]}, {"name": "X", "paths": ["b"]}]})
    assert architecture.validate_policy(POLICY)["layers"][0]["name"] == "domain"


def test_inner_layer_importing_outer_is_violation_type_imports_by_policy(env):
    graph = graph_of(LAYERED)
    found = architecture.violations(graph, architecture.validate_policy(POLICY), read=LAYERED.get)["violations"]
    pairs = {(item["path"], item["target"], item["kind"]) for item in found}
    # по умолчанию строго: импорт только для типов тоже связывает слои
    assert pairs == {("app/domain/order.py", "app/infra/db.py", "import"), ("app/domain/order.py", "app/api/routes.py", "type_import")}
    assert found[0]["line_start"] in {3, 4}
    lenient = architecture.violations(graph, architecture.validate_policy({**POLICY, "allow_type_imports": True}), read=LAYERED.get)
    assert {(item["target"], item["kind"]) for item in lenient["violations"]} == {("app/infra/db.py", "import")}


def test_architecture_gate_fails_only_on_new_edges(env):
    graph = graph_of(LAYERED)
    architecture.set_policy(env.project_id, POLICY)
    old = architecture.architecture_gate(graph, env.root, project_id=env.project_id, owner=OWNER,
                                         paths=["app/domain/order.py"], full=False,
                                         head_text=lambda path: LAYERED[path], read=LAYERED.get)
    assert old.status == "passed" and old.summary["new"] == 0
    new = architecture.architecture_gate(graph, env.root, project_id=env.project_id, owner=OWNER,
                                         paths=["app/domain/order.py"], full=False,
                                         head_text=lambda path: "", read=LAYERED.get)
    assert new.status == "failed" and new.summary["new"] == 2
    assert {item["kind"] for item in new.findings} == {"new_violation"}


def test_no_policy_means_unavailable_and_proposals_never_apply_themselves(env):
    graph = graph_of(LAYERED)
    gate = architecture.architecture_gate(graph, env.root, project_id=env.project_id, owner=OWNER,
                                          paths=["app/domain/order.py"], full=True, read=LAYERED.get)
    assert gate.status in {"skipped", "unavailable"}
    saved = architecture.save_proposal(env.project_id, OWNER, "три слоя", POLICY, "по графу")
    assert saved["label"] == architecture.PROPOSAL_LABEL
    assert architecture.get_policy(env.project_id) is None
    assert architecture.evaluate_proposal(graph, POLICY, read=LAYERED.get)["violations"] >= 1
    assert architecture.delete_proposal(env.project_id, OWNER, saved["id"])
    assert architecture.proposals(env.project_id, OWNER) == []


def test_suggest_layers_orders_by_dependency_direction():
    texts = {"core/__init__.py": "", "core/model.py": "", "svc/__init__.py": "", "svc/logic.py": "from core import model\n",
             "web/__init__.py": "", "web/view.py": "from svc import logic\nfrom core import model\n"}
    layers = architecture.suggest_layers(graph_of(texts))["layers"]
    names = [layer["paths"] for layer in layers]
    flat = [path for paths in names for path in paths]
    assert flat.index(next(p for p in flat if p.startswith("core"))) < flat.index(next(p for p in flat if p.startswith("web")))


# ---------- сигналы

def test_signals_flag_weak_tests_and_swallowed_errors(env):
    write(env.root, "tests/test_weak.py", "def test_nothing():\n    result = 1 + 1\n")
    write(env.root, "svc.py", "def load():\n    try:\n        return open('x').read()\n    except Exception:\n        pass\n")
    result = signals.signals_gate(env.root, ["tests/test_weak.py", "svc.py"],
                                  before_texts={"tests/test_weak.py": None, "svc.py": None})
    kinds = {item["kind"] for item in result.findings}
    assert result.status == "failed"
    assert any("test" in kind for kind in kinds) and any("swallow" in kind or "except" in kind for kind in kinds), kinds


# ---------- red / green, предпосылка, журнал

def _result(status: str, output: str, check: str = "python:test:pytest@") -> dict:
    return {"id": check, "kind": "test", "status": status, "output_tail": output, "diagnostics": []}


def test_red_counts_only_assertion_failures_with_matching_reason():
    assert not workflow.red_verdict([_result("failed", "ModuleNotFoundError: No module named 'calc'")], None)["verified"]
    assert not workflow.red_verdict([_result("failed", "SyntaxError: invalid syntax")], None)["verified"]
    assert not workflow.red_verdict([_result("passed", "1 passed")], None)["verified"]
    ok = workflow.red_verdict([_result("failed", "assert grade(90) == 'A'\nAssertionError: 'B' != 'A'")],
                              "assert grade 90")
    assert ok["verified"] and ok["reason_matched"] is True
    other = workflow.red_verdict([_result("failed", "assert total == 3\nAssertionError")], "ожидаем `rounding error`")
    assert not other["verified"] and other["reason_matched"] is False


def test_red_then_green_and_premise_challenge_after_two_versions(env):
    verdict = workflow.red_verdict([_result("failed", "AssertionError: expected 'A'")], None)
    workflow.record_red(env.turn_id, env.project_id, "v1", verdict, "граница A")
    response = workflow.record_checks(env.turn_id, "v2", [_result("passed", "1 passed")])
    assert response["red_green"] == ["python:test:pytest@"]
    # одна и та же проверка падает на двух разных версиях кода - требуется атака на предпосылку
    workflow.record_checks(env.turn_id, "v3", [_result("failed", "AssertionError")])
    assert workflow.premise_block(env.turn_id, None) is None
    workflow.record_checks(env.turn_id, "v4", [_result("failed", "AssertionError")])
    assert "premise" in (workflow.premise_block(env.turn_id, None) or "")
    weak = workflow.apply_plan_update(env.turn_id, env.project_id, {"premise": {"assumption": "кэш", "new_approach": "x"}})
    assert weak["workflow_notes"] and workflow.premise_block(env.turn_id, None)
    workflow.apply_plan_update(env.turn_id, env.project_id, {"premise": {
        "assumption": "ошибка в кэше", "refuting_facts": ["кэш выключен в тесте"], "new_approach": "смотрю сериализацию"}})
    assert workflow.premise_block(env.turn_id, None) is None


def test_steps_need_checks_stage_and_decisions_survive_compaction(env):
    qa = {"results": [{"id": "python:test:pytest@", "status": "passed"}]}
    out = workflow.apply_plan_update(env.turn_id, env.project_id, {
        "stage": "make_it_right",
        "steps": [{"title": "граница A", "status": "completed", "check": "python:test:pytest@"},
                  {"title": "отчёт", "status": "completed", "check": "python:lint:ruff@"}],
        "root_cause": {"symptom": "B вместо A", "chain": ["90 даёт B", "условие > вместо >="], "cause": "строгое сравнение"},
        "decision": {"kind": "approach", "summary": "правлю условие, а не тест"},
    }, qa=qa, current=True)
    assert out["workflow"]["stage"] == "make_it_right"
    assert [unit["verified"] for unit in out["workflow"]["units"]] == [True, False]
    assert any("отчёт" in note for note in out["workflow_notes"])
    # следующий ход того же чата получает выжимку даже после сжатия истории
    hint = workflow.context_hint(env.chat_id)
    assert hint["stage"] == "make_it_right" and hint["root_cause"] == "строгое сравнение"
    assert any("правлю условие" in item for item in hint["decisions"])


# ---------- бюджеты и политика

def test_budget_levels_and_off_mode(env):
    light, pro, top = budgets.budget_for("light"), budgets.budget_for("pro"), budgets.budget_for("max")
    assert light["max_mutants"] == 0 and "mutation" not in light["auto_gates"]
    assert 0 < pro["max_mutants"] < top["max_mutants"] <= budgets.MANUAL_LIMITS["max_mutants"]
    assert top["coverage"] == "collect" and pro["coverage"] == "existing"
    assert budgets.budget_for("unknown")["level"] == "pro"
    assert budgets.set_mode("off") == "off" and budgets.automatic("max") is None
    assert budgets.set_mode("economy") == "economy" and budgets.budget_for("max")["level"] == "light"
    assert budgets.set_mode("strict") == "strict" and budgets.budget_for("light")["level"] == "max"
    with pytest.raises(ValueError):
        budgets.set_mode("turbo")
    budgets.set_mode("auto")


def test_policy_quality_run_static_is_allowed_processes_need_confirmation(env, monkeypatch):
    from koddi.policy.engine import decide

    monkeypatch.setattr("koddi.policy.grants.permission_profile", lambda project_id=None, **_: "ask")
    assert decide("quality.run", {"gates": ["crap", "dry"]}, env.root, env.project_id).action == "allow"
    assert decide("quality.run", {"gates": ["mutation"]}, env.root, env.project_id).action == "ask"
    assert decide("quality.run", {"gates": ["crap"], "coverage": "collect"}, env.root, env.project_id).action == "ask"
    assert decide("quality.run", {"paths": ["../outside.py"]}, env.root, env.project_id).action == "block"
    monkeypatch.setattr("koddi.policy.grants.permission_profile", lambda project_id=None, **_: "read-only")
    assert decide("quality.run", {"gates": ["mutation"]}, env.root, env.project_id).action == "block"
    assert decide("quality.run", {"gates": ["signals"]}, env.root, env.project_id).action == "allow"


def test_agent_cannot_accept_its_own_findings(env):
    from koddi.quality.engine import quality_tool

    result = quality_tool(env.runner, {"decisions": [{"gate": "mutation", "key": "k", "decision": "accepted"}]})
    assert result["decisions"] == [{"key": "k", "ok": False, "error": result["decisions"][0]["error"]}]
    assert "только человек" in result["decisions"][0]["error"]
    with storage.db() as conn:
        row = conn.execute("SELECT decision FROM quality_baseline WHERE item_key='k'").fetchone()
    assert row is None or row["decision"] != "accepted"


def test_gate_result_rejects_unknown_status():
    with pytest.raises(ValueError):
        GateResult("crap", "green")
    for status in ("passed", "failed", "skipped", "unavailable", "cancelled", "error"):
        assert GateResult("crap", status).status == status


# ---------- git: атомарные коммиты и worktree

def test_git_add_requires_explicit_files_and_commit_keeps_user_staged_work(tmp_path):
    from koddi.projects import gitsafe

    root = git_repo(tmp_path / "repo")
    write(root, "base.txt", "0\n")
    git(root, "add", "base.txt")
    git(root, "commit", "-qm", "base")
    for bad in (["."], ["*.py"], [":/"], ["-A"], []):
        assert not gitsafe.add(root, bad)["ok"]
    write(root, "mine.py", "x = 1\n")
    write(root, "user.txt", "человек готовит свой коммит\n")
    git(root, "add", "user.txt")
    assert gitsafe.add(root, ["mine.py"])["ok"]
    refused = gitsafe.commit(root, "моё", paths=None, body=None, own={"mine.py"})
    assert not refused["ok"] and "user.txt" in refused["error"]
    done = gitsafe.commit(root, "моё", paths=["mine.py"], body=None, own={"mine.py"})
    assert done["ok"] and done["committed"] == ["mine.py"] and done["kept_staged"] == ["user.txt"]
    assert gitsafe.staged(root) == ["user.txt"]
    assert git(root, "show", "--name-only", "--format=", "HEAD").split() == ["mine.py"]
    # повтор того же коммита после обрыва не создаёт второй и не считается ошибкой
    count = git(root, "rev-list", "--count", "HEAD")
    retry = gitsafe.commit(root, "моё", paths=["mine.py"], body=None, own={"mine.py"})
    assert retry["ok"] and retry["already_committed"] and git(root, "rev-list", "--count", "HEAD") == count
    assert not gitsafe.commit(root, "другое", paths=["mine.py"], body=None, own={"mine.py"})["ok"]
    assert not gitsafe.commit(root, "x" * 101, paths=["mine.py"], body=None, own={"mine.py"})["ok"]
    assert not gitsafe.commit(root, "чужое", paths=["base.txt"], body=None, own={"mine.py"})["ok"]


def test_worktree_is_idempotent_and_cleanup_keeps_dirty_or_ahead(env):
    from koddi.projects import gitsafe

    git(env.root, "add", "calc.py")
    git(env.root, "commit", "-qm", "base")
    clean = env.root.parent / "wt-clean"
    dirty = env.root.parent / "wt-dirty"
    ahead = env.root.parent / "wt-ahead"
    for path, branch in ((clean, "clean"), (dirty, "dirty"), (ahead, "ahead")):
        assert gitsafe.ensure_worktree(env.root, path, branch)["ok"]
        with storage.db() as conn:
            conn.execute("INSERT INTO worktrees(project_id,name,path,branch) VALUES (?,?,?,?)",
                         (env.project_id, branch, str(path), branch))
    again = gitsafe.ensure_worktree(env.root, clean, "clean")
    assert again["ok"] and again["reused"]
    assert not gitsafe.ensure_worktree(env.root, clean, "other")["ok"]
    write(dirty, "calc.py", "changed\n")
    write(ahead, "new.txt", "1\n")
    git(ahead, "add", "new.txt")
    git(ahead, "commit", "-qm", "ahead")
    plan = gitsafe.cleanup(env.project_id, env.root, dry_run=True)
    assert [item["branch"] for item in plan["removed"]] == ["clean"]
    assert clean.exists()
    kept = {Path(item["path"]).name: item["reason"] for item in plan["kept"]}
    assert "незакоммиченные" in kept["wt-dirty"] and "коммиты" in kept["wt-ahead"]
    done = gitsafe.cleanup(env.project_id, env.root, dry_run=False)
    assert [item["branch"] for item in done["removed"]] == ["clean"]
    assert not clean.exists() and dirty.exists() and ahead.exists()
    assert "clean" in git(env.root, "branch", "--list", "clean")


# ---------- один писатель

def test_one_writer_per_workspace_and_stale_lock_is_reclaimed(env):
    from koddi.changes import writer

    other = env.turn()
    writer.claim(env.root, env.turn_id)
    writer.claim(env.root, env.turn_id)  # повторно тем же ходом - можно
    with pytest.raises(writer.WriterBusy):
        writer.claim(env.root, other)
    with storage.db() as conn:
        conn.execute("UPDATE turns SET status='failed' WHERE id=?", (env.turn_id,))
    writer.claim(env.root, other)
    assert writer.holder(env.root).turn_id == other
    writer.release(other)
    assert writer.holder(env.root) is None


def test_parallel_writers_get_exactly_one_lock(env):
    from koddi.changes import writer

    turns = [env.turn() for _ in range(6)]
    wins: list[int] = []
    barrier = threading.Barrier(len(turns))

    def grab(turn_id: int) -> None:
        barrier.wait()
        try:
            writer.claim(env.root, turn_id)
            wins.append(turn_id)
        except writer.WriterBusy:
            pass

    threads = [threading.Thread(target=grab, args=(turn,)) for turn in turns]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(wins) == 1
    writer.release(wins[0])


# ---------- совместимость закреплённых схем

def test_sessions_pinned_at_previous_release_stay_compatible():
    from koddi.agents.orchestrator import CONTROL_TOOLS
    from koddi.dokky.coding import compatible_tools
    from koddi.tools.schemas import TOOL_SCHEMAS

    pinned = json.loads((Path(__file__).parent / "fixtures" / "pinned_tool_schemas_v2.json").read_text(encoding="utf-8"))
    selected = compatible_tools(pinned, [*TOOL_SCHEMAS, *CONTROL_TOOLS])
    assert [item["function"]["parameters"] for item in selected] == [item["function"]["parameters"] for item in pinned]
    assert "quality.run" not in {item["function"]["name"] for item in selected}


def test_new_ub_arguments_are_optional_additions():
    from koddi.agents.orchestrator import CONTROL_TOOLS
    from koddi.dokky.coding import compatible_tools
    from koddi.tools.schemas import TOOL_SCHEMAS_BY_NAME

    current = {**TOOL_SCHEMAS_BY_NAME, **{item["function"]["name"]: item for item in CONTROL_TOOLS}}
    removed = {"qa.run": ["expect", "reason"], "git.commit": ["paths", "body"],
               "plan.update": ["stage", "root_cause", "premise", "decision"]}
    old = []
    for name, keys in removed.items():
        schema = deepcopy(current[name])
        params = schema["function"]["parameters"]
        for key in keys:
            assert key in params["properties"], (name, key)
            assert key not in params.get("required", []), (name, key)
            params["properties"].pop(key)
        old.append(schema)
    selected = compatible_tools(old, list(current.values()))
    assert [item["function"]["parameters"] for item in selected] == [item["function"]["parameters"] for item in old]


# ---------- критерии приёмки: граница, legacy, повтор

def _ladder(n: int, name: str = "f") -> str:
    return f"def {name}(x):\n" + "".join(f"    if x == {k}:\n        return {k}\n" for k in range(n - 1)) + "    return -1\n"


def test_crap_equal_to_threshold_does_not_pass(env):
    write(env.root, "ten.py", _ladder(10))
    scope = metrics.build_scope(env.root, ["ten.py"], before_texts={"ten.py": None})
    assert scope.changed[0].complexity == 10
    lines = set(range(1, 30))
    # полное покрытие: CRAP = CC = 10, а правило строгое «< 10»
    result = metrics.crap_gate(scope, coverage_data=_coverage(env.root, "ten.py", lines, lines),
                               project_id=env.project_id, owner=OWNER)
    assert result.findings[0]["value"] == 10 and result.status == "failed"


def test_untouched_legacy_debt_warns_but_does_not_fail(env):
    write(env.root, "legacy.py", _ladder(14, "old") + "\n\ndef helper(a):\n    return a + 1\n")
    git(env.root, "add", "legacy.py")
    git(env.root, "commit", "-qm", "legacy")
    write(env.root, "legacy.py", _ladder(14, "old") + "\n\ndef helper(a):\n    return a + 2\n")
    scope = metrics.build_scope(env.root, ["legacy.py"])
    assert [item.symbol for item in scope.changed] == ["helper"]
    lines = set(range(1, 60))
    result = metrics.crap_gate(scope, coverage_data=_coverage(env.root, "legacy.py", lines, lines),
                               project_id=env.project_id, owner=OWNER)
    assert result.status == "passed"
    assert [item["kind"] for item in result.findings] == ["legacy_debt"]


def test_dry_skips_trivial_blocks(env):
    write(env.root, "a.py", "def a(x):\n    return x + 1\n")
    git(env.root, "add", "a.py")
    git(env.root, "commit", "-qm", "a")
    write(env.root, "b.py", "def b(y):\n    return y + 1\n")
    scope = metrics.build_scope(env.root, ["b.py"], before_texts={"b.py": None})
    assert metrics.dry_gate(scope, project_id=env.project_id, owner=OWNER).status == "skipped"


def test_repeated_gate_run_reuses_stored_result(env):
    from koddi.quality import engine

    write(env.root, "grade.py", BRANCHY)
    budget = budgets.budget_for("light")
    first = engine.run_gates(env.runner, gates=["crap"], paths=["grade.py"], full=False, budget=budget,
                             coverage_mode="none", before_texts={"grade.py": None})
    again = engine.run_gates(env.runner, gates=["crap"], paths=["grade.py"], full=False, budget=budget,
                             coverage_mode="none", before_texts={"grade.py": None})
    assert first[0].run_id == again[0].run_id and again[0].summary.get("reused")
    with storage.db() as conn:
        assert conn.execute("SELECT COUNT(*) FROM quality_runs WHERE gate='crap'").fetchone()[0] == 1
    # другая версия кода - новый прогон
    write(env.root, "grade.py", BRANCHY.replace('"D"', '"E"'))
    third = engine.run_gates(env.runner, gates=["crap"], paths=["grade.py"], full=False, budget=budget,
                             coverage_mode="none", before_texts={"grade.py": None})
    assert third[0].run_id != first[0].run_id


def test_broken_mutation_baseline_keeps_previous_snapshot(mutation_project):
    from koddi.quality import engine

    env = mutation_project.env
    budget = {**budgets.budget_for("pro"), "max_mutants": 12}
    good = engine.run_gates(env.runner, gates=["mutation"], paths=["calc.py"], full=True, budget=budget,
                            coverage_mode="none")[0]
    assert good.status == "failed" and good.summary["survived"] > 0
    survived = architecture.heatmap(env.project_id, OWNER)["calc.py"]["survived"]
    write(env.root, "tests/test_calc.py", WEAK + "\n\ndef test_broken():\n    assert False\n")
    broken = engine.run_gates(env.runner, gates=["mutation"], paths=["calc.py", "tests/test_calc.py"], full=True,
                              budget=budget, coverage_mode="none")[0]
    assert broken.status == "unavailable"
    # провал базового прогона не затирает последний настоящий результат
    assert architecture.heatmap(env.project_id, OWNER)["calc.py"]["survived"] == survived


def test_proposal_does_not_touch_sources(env):
    for path, text in LAYERED.items():
        write(env.root, path, text)
    before = {path: (env.root / path).read_bytes() for path in LAYERED}
    graph = graph_of(LAYERED)
    saved = architecture.save_proposal(env.project_id, OWNER, "слои", POLICY)
    architecture.evaluate_proposal(graph, saved["policy"], read=LAYERED.get)
    assert {path: (env.root / path).read_bytes() for path in LAYERED} == before
    assert architecture.get_policy(env.project_id) is None


def test_agent_has_no_push_or_force_tools():
    from koddi.tools.schemas import TOOL_SCHEMAS_BY_NAME

    assert not {name for name in TOOL_SCHEMAS_BY_NAME if "push" in name}
    commit = TOOL_SCHEMAS_BY_NAME["git.commit"]["function"]["parameters"]["properties"]
    assert not {"amend", "no_verify", "force"} & set(commit)


# ---------- исправления после ревью

def test_lines_follow_ast_rules_and_deletion_at_function_end_marks_it():
    from koddi.quality.functions import changed_lines

    text = "x = '\u2028'\ndef f():\n    return 1\n"
    assert functions_of("m.py", text)[0].line_start == 2
    assert changed_lines(None, text) == {1, 2, 3}
    before = "def f():\n    a = 1\n    b = 2\n\ndef g():\n    return 2\n"
    after = "def f():\n    a = 1\n\ndef g():\n    return 2\n"
    from koddi.quality.functions import touched as touched_of

    touched = touched_of(functions_of("m.py", after), changed_lines(before, after))
    assert [item.symbol for item in touched] == ["f"]


def test_property_getter_and_setter_get_distinct_keys():
    text = ("class A:\n    @property\n    def v(self):\n        return 1\n\n"
            "    @v.setter\n    def v(self, value):\n        self._v = value\n")
    keys = [item.key for item in functions_of("a.py", text)]
    assert len(keys) == 2 and len(set(keys)) == 2


def test_function_coverage_counts_only_body_statements():
    text = "@decorate\ndef f(x):\n    return x\n"
    function = functions_of("d.py", text)[0]
    # строки декоратора и def выполнены при импорте, тело - нет: покрытие 0, а не 2/3
    coverage = FileCoverage({1, 2, 3}, {1, 2}, "test", time.time())
    assert metrics._function_coverage(function, coverage) == (0.0, "ok")


def test_unparsable_file_is_noted_not_fatal(env, monkeypatch):
    write(env.root, "ok.py", BRANCHY)
    write(env.root, "deep.py", "def f():\n    return 1\n")
    real = metrics.functions_of

    def flaky(path, text):
        if path == "deep.py":
            raise RecursionError("too deep")
        return real(path, text)

    monkeypatch.setattr(metrics, "functions_of", flaky)
    scope = metrics.build_scope(env.root, ["ok.py", "deep.py"], before_texts={"ok.py": None, "deep.py": None})
    assert scope.paths == ["ok.py"] and any("deep.py" in note for note in scope.notes)


def test_dry_truncated_without_findings_is_unavailable(env):
    for index in range(4):
        write(env.root, f"m{index}.py", f"def f{index}(x):\n" + "".join(
            f"    x = x * {index + 2} + {line}\n" for line in range(6)) + "    return x\n")
    scope = metrics.build_scope(env.root, ["m0.py"], before_texts={"m0.py": None})
    result = metrics.dry_gate(scope, project_id=env.project_id, owner=OWNER, universe_limit=1, min_lines=3)
    # сравнение обрезано: без открытых пар это «не измерено», а не «чисто»
    assert result.summary["truncated"]
    assert result.status == ("failed" if result.summary["open_candidates"] else "unavailable")


def test_transient_unavailable_does_not_occupy_the_key(env):
    from koddi.quality.contract import persist

    first = persist(env.project_id, OWNER, None, GateResult("mutation", "unavailable", error="baseline_failed"), "k1")
    again = persist(env.project_id, OWNER, None, GateResult("mutation", "unavailable", error="baseline_failed"), "k1")
    assert first.run_id != again.run_id
    stable = persist(env.project_id, OWNER, None, GateResult("crap", "unavailable"), "k2")
    assert persist(env.project_id, OWNER, None, GateResult("crap", "unavailable"), "k2").run_id == stable.run_id


def test_gate_key_changes_with_head_and_keeps_confidence(env):
    from koddi.quality import engine

    write(env.root, "grade.py", BRANCHY)
    git(env.root, "add", "calc.py")
    git(env.root, "commit", "-qm", "base")
    budget = budgets.budget_for("light")
    first = engine.run_gates(env.runner, gates=["crap"], paths=["grade.py"], full=False, budget=budget,
                             coverage_mode="none", before_texts={"grade.py": None})
    reused = engine.run_gates(env.runner, gates=["crap"], paths=["grade.py"], full=False, budget=budget,
                              coverage_mode="none", before_texts={"grade.py": None})
    assert reused[0].run_id == first[0].run_id and reused[0].confidence == first[0].confidence
    write(env.root, "other.py", "x = 1\n")
    git(env.root, "add", "other.py")
    git(env.root, "commit", "-qm", "next")
    moved = engine.run_gates(env.runner, gates=["crap"], paths=["grade.py"], full=False, budget=budget,
                             coverage_mode="none", before_texts={"grade.py": None})
    assert moved[0].run_id != first[0].run_id


def test_mutation_out_of_time_is_unavailable_and_not_cached(mutation_project):
    result = _mutate(mutation_project, wall_seconds=0)
    assert result.status == "unavailable" and result.error == "budget"


def test_agent_review_does_not_override_human_acceptance(env):
    from koddi.quality.engine import quality_tool

    assert metrics.set_decision(env.project_id, OWNER, "dry", "pair", "accepted")
    result = quality_tool(env.runner, {"decisions": [{"gate": "dry", "key": "pair", "decision": "review"}]})
    assert not result["decisions"][0]["ok"]
    with storage.db() as conn:
        row = conn.execute("SELECT decision FROM quality_baseline WHERE item_key='pair'").fetchone()
    assert row["decision"] == "accepted"


def test_counted_test_failure_mentioning_missing_command_is_a_failure(tmp_path):
    check = checks.Check("python:test:pytest@", "test", "pytest", "", ["pytest"])
    output = "E   FileNotFoundError: command not found: convert\n1 failed, 3 passed in 0.2s"
    assert checks.classify(check, {"returncode": 1, "stdout": output}, tmp_path)["status"] == "failed"
    assert checks.classify(check, {"returncode": 1, "stderr": "zsh: command not found: pytest"}, tmp_path)["status"] == "environment"


def test_workflow_lists_are_capped_without_breaking_json(env):
    workflow.save(env.turn_id, {"stage": "work", "green": [{"check": str(index)} for index in range(100)],
                                "failures": {str(index): 1 for index in range(100)}})
    stored = workflow.load(env.turn_id)
    assert stored["stage"] == "work" and len(stored["green"]) == workflow.LIST_LIMITS["green"]
    assert stored["green"][-1] == {"check": "99"} and len(stored["failures"]) == 40


def test_git_paths_are_literal_and_subfolder_workspace_commits(tmp_path):
    from koddi.projects import gitsafe

    repo = git_repo(tmp_path / "repo")
    write(repo, "base.txt", "0\n")
    git(repo, "add", "base.txt")
    git(repo, "commit", "-qm", "base")
    app = repo / "app"
    write(app, "[id].tsx", "export default 1\n")
    write(app, "файл.py", "x = 1\n")
    assert gitsafe.add(app, ["[id].tsx", "файл.py"])["ok"]
    assert sorted(gitsafe.staged(app)) == ["[id].tsx", "файл.py"]
    done = gitsafe.commit(app, "страница", paths=["[id].tsx", "файл.py"], body=None, own={"[id].tsx", "файл.py"})
    assert done["ok"], done
    assert sorted(git(repo, "-c", "core.quotePath=false", "show", "--name-only", "--format=", "HEAD").split()) \
        == ["app/[id].tsx", "app/файл.py"]
    retry = gitsafe.commit(app, "страница", paths=["[id].tsx", "файл.py"], body=None, own={"[id].tsx", "файл.py"})
    assert retry["ok"] and retry["already_committed"]
    from koddi.quality.functions import head_text

    assert head_text(app, "файл.py") == "x = 1\n"


def test_worktree_target_file_refused_and_unsafe_removals_blocked(tmp_path):
    from koddi.projects import gitsafe

    repo = git_repo(tmp_path / "repo")
    write(repo, "base.txt", "0\n")
    git(repo, "add", "base.txt")
    git(repo, "commit", "-qm", "base")
    write(tmp_path, "busy", "file\n")
    assert not gitsafe.ensure_worktree(repo, tmp_path / "busy", None)["ok"]
    assert not gitsafe.ensure_worktree(repo, tmp_path / "busy", "feature")["ok"]
    assert not gitsafe.remove_worktree(repo, repo)["ok"]
    detached = tmp_path / "detached"
    assert gitsafe.ensure_worktree(repo, detached, None)["ok"]
    write(detached, "new.txt", "1\n")
    git(detached, "add", "new.txt")
    git(detached, "commit", "-qm", "lost")
    refused = gitsafe.remove_worktree(repo, detached, force=True)
    assert not refused["ok"] and detached.exists()
    clean = tmp_path / "clean"
    assert gitsafe.ensure_worktree(repo, clean, None)["ok"]
    assert gitsafe.remove_worktree(repo, clean)["ok"] and not clean.exists()


def test_knowledge_export_never_follows_links(tmp_path):
    from koddi.knowledge.service import _contained

    base = tmp_path / "knowledge"
    (base / "decisions").mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    write(outside, "secret.md", "secret\n")
    (base / "decisions" / "link.md").symlink_to(outside / "secret.md")
    (base / "escape").symlink_to(outside, target_is_directory=True)
    assert _contained(base, base / "decisions" / "note.md")
    assert not _contained(base, base / "decisions" / "link.md")
    assert not _contained(base, base / "escape" / "secret.md")


def test_library_docs_do_not_read_through_links_outside_project(tmp_path):
    from koddi.libdocs.service import _read

    root = tmp_path / "project"
    write(root, "node_modules/pkg/README.md", "inside\n")
    write(tmp_path, "private.md", "private\n")
    (root / "node_modules/pkg/CHANGELOG.md").symlink_to(tmp_path / "private.md")
    assert _read(root / "node_modules/pkg/README.md", root) == "inside\n"
    assert _read(root / "node_modules/pkg/CHANGELOG.md", root) is None


def test_server_web_search_falls_back_on_unexpected_response(monkeypatch):
    import asyncio

    from koddi.dokky import web

    class Response:
        def json(self):
            return ["not", "a", "dict"]

    async def ready():
        return True

    async def request(*_args, **_kwargs):
        return Response()

    async def local(query, limit):
        return {"ok": True, "query": query, "results": [], "source": "local"}

    monkeypatch.setattr(web, "_server_ready", ready)
    monkeypatch.setattr(web.dokky, "request", request)
    monkeypatch.setattr(web, "local_search", local)
    assert asyncio.run(web.web_search("python", 3))["source"] == "local"


def test_typescript_lines_match_language_service_and_bad_utf8_is_refused(tmp_path):
    from koddi.codeintel import typescript as tsl
    from koddi.codeintel.pyproject import SymbolError
    from koddi.codeintel.refactor import _raw

    text = "a\r\nb\u2028c\rd\n"
    lines, breaks = tsl.split_keep(text)
    assert lines == ["a", "b", "c", "d", ""] and tsl.join_keep(lines, breaks) == text
    assert tsl.line_of(text, 3) == "c"
    (tmp_path / "bad.ts").write_bytes(b"const x = '\xff';\n")
    with pytest.raises(SymbolError):
        _raw(tmp_path, "bad.ts")


def test_retrieve_cache_rechecks_files(tmp_path):
    import hashlib

    from koddi.context.retrieve import _still_fresh

    write(tmp_path, "a.py", "x = 1\n")
    digest = hashlib.sha256(b"x = 1\n").hexdigest()
    assert _still_fresh(tmp_path, {"items": [{"path": "a.py", "sha256": digest}]})
    write(tmp_path, "a.py", "x = 2\n")
    assert not _still_fresh(tmp_path, {"items": [{"path": "a.py", "sha256": digest}]})


def test_script_chunks_use_the_same_line_rule_as_declarations():
    from koddi.projectmap.chunks import chunk_ranges

    text = "// a\rfunction f(x) {\r  if (x) {\r    return 1\r  }\r  return 2\r}\r"
    chunks = chunk_ranges("typescript", text)
    assert any(item[2] == "f" and item[0] == 2 and item[1] == 7 for item in chunks), chunks


def test_failed_overlay_copy_leaves_nothing_behind(env, monkeypatch):
    def broken(*_args, **_kwargs):
        raise OSError("no space left")

    monkeypatch.setattr(mutation.shutil, "copy2", broken)
    with pytest.raises(OSError):
        mutation.make_overlay(env.root)
    assert not [item for item in mutation.overlays_root().glob("mut-*")]
