"""Детерминированный бенчмарк качества: прежний текстовый подход против новых инструментов на одних фикстурах.

Модель не вызывается: токены, стоимость и задержка реальной модели здесь не измеряются (live-бенчмарк
отдельно, только по бюджету). Всё идёт во временной папке со своей базой, данные пользователя не трогаются.

    python scripts/benchmark_quality.py [--out result.json] [--corpus backend/koddi]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

SHOP = {
    "shop/__init__.py": "",
    "shop/cart.py": "class Cart:\n    def __init__(self):\n        self.items = []\n\n    def total(self):\n"
                    "        return sum(item.price for item in self.items)\n\n\nclass Wishlist:\n    def total(self):\n        return 0\n",
    "shop/checkout.py": "from shop.cart import Cart\n\n\ndef checkout(cart: Cart):\n    total = cart.total()\n    return {'total': total}\n\n\n"
                        "def wishlist_total(wishlist):\n    # total of the wishlist is not money\n    return wishlist.total()\n",
    "shop/report.py": "from shop.cart import Cart as Basket\n\n\ndef report(basket: Basket) -> str:\n    return f'total={basket.total()}'\n",
}
# правильные места Cart.total (файл, строка) и итог переименования в grand_total
TRUTH = {("shop/cart.py", 5), ("shop/checkout.py", 5), ("shop/report.py", 5)}
RENAMED = {
    "shop/cart.py": SHOP["shop/cart.py"].replace("    def total(self):\n        return sum", "    def grand_total(self):\n        return sum"),
    "shop/checkout.py": SHOP["shop/checkout.py"].replace("cart.total()", "cart.grand_total()"),
    "shop/report.py": SHOP["shop/report.py"].replace("basket.total()", "basket.grand_total()"),
}
# запрос → файлы, где есть ответ (корпус - копия backend/koddi)
QUERIES = [
    ("откат набора изменений с проверкой конфликтов файлов", ("koddi/changes/service.py", "koddi/changes/checkpoints.py")),
    ("опасные маркеры shell команды требуют подтверждения", ("koddi/policy/engine.py",)),
    ("находки ревьюера вне изменённых файлов отбрасываются", ("koddi/quality/reviewer.py",)),
    ("frontmatter SKILL.md name description", ("koddi/skills/project.py",)),
    ("PageRank важность файлов по импортам", ("koddi/projectmap/graph.py",)),
    ("версия пакета из lockfile и node_modules", ("koddi/libdocs/service.py",)),
]
CALC = {
    "pyproject.toml": "[project]\nname = 'calc'\n\n[tool.pytest.ini_options]\ntestpaths = ['tests']\npythonpath = ['.']\n\n"
                      "[tool.ruff]\nline-length = 120\n",
    "calc.py": "def add(a, b):\n    return a + b\n",
    "tests/test_calc.py": "from calc import add\n\n\ndef test_add():\n    assert add(2, 3) == 5\n",
}
# посеянные дефекты: прежняя проверка (компиляция Python) пропускает все три
BUGS = [
    ("wrong_operator", "calc.py", "return a + b", "return a - b"),
    ("unused_import", "calc.py", "def add(a, b):", "import os\n\n\ndef add(a, b):"),
    ("broken_contract", "calc.py", "def add(a, b):\n    return a + b", "def add(a, b, c):\n    return a + b + c"),
]


def write_tree(root: Path, files: dict[str, str]) -> None:
    for relative, text in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


def prf(found: set, truth: set) -> dict:
    hits = len(found & truth)
    return {"precision": round(hits / len(found), 3) if found else 0.0, "recall": round(hits / len(truth), 3) if truth else 0.0,
            "found": len(found), "false_positives": len(found - truth)}


def grep_lines(root: Path, name: str) -> set:
    pattern = re.compile(rf"\b{re.escape(name)}\b")
    found = set()
    for path in root.rglob("*.py"):
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if pattern.search(line):
                found.add((path.relative_to(root).as_posix(), number))
    return found


def new_project(base: Path, name: str, files: dict[str, str] | None = None, source: Path | None = None):
    from koddi.projects import add_project
    from koddi.tools.runner import ToolRunner

    root = base / name
    if source is not None:
        shutil.copytree(source, root / source.name, ignore=shutil.ignore_patterns("__pycache__", "web-next", "*.pyc"))
    write_tree(root, files or {})
    project = add_project(str(root))
    return root.resolve(), project["id"], ToolRunner(root.resolve(), project["id"])


def bench_symbols(base: Path) -> dict:
    root, _, runner = new_project(base, "shop", SHOP)
    baseline = grep_lines(root, "total")
    semantic = runner.run("code.references", {"symbol": "shop/cart.py:Cart.total"})
    upgraded = {(item["path"], item["line"]) for item in semantic.get("references", [])}
    text_root, _, _ = new_project(base, "shop_text", SHOP)
    for path in text_root.rglob("*.py"):
        path.write_text(re.sub(r"\btotal\b", "grand_total", path.read_text(encoding="utf-8")), encoding="utf-8")
    # wishlist.total() - получатель неизвестного типа: первый вызов не угадывает и просит решения
    asked = runner.run("refactor.rename", {"old": "shop/cart.py:Cart.total", "new": "grand_total"})
    renamed = runner.run("refactor.rename", {"old": "shop/cart.py:Cart.total", "new": "grand_total", "include_candidates": False})

    def exact(tree: Path) -> int:
        return sum((tree / path).read_text(encoding="utf-8") == text for path, text in RENAMED.items())

    def wrong_lines(tree: Path) -> int:
        count = 0
        for path, text in RENAMED.items():
            now = (tree / path).read_text(encoding="utf-8").splitlines()
            count += sum(1 for a, b in zip(now, text.splitlines()) if a != b)
        return count

    return {
        "references": {"baseline_text_search": prf(baseline, TRUTH), "upgraded_code_references": prf(upgraded, TRUTH),
                       "upgraded_candidates_not_counted": len(semantic.get("candidates", []))},
        "rename": {"baseline_word_replace": {"files_exact": exact(text_root), "wrong_lines": wrong_lines(text_root)},
                   "upgraded_refactor_rename": {"asked_about_unknown_receiver": asked.get("error_code") == "needs_review",
                                                "ok": bool(renamed.get("ok")), "files_exact": exact(root),
                                                "wrong_lines": wrong_lines(root)}, "files_total": len(RENAMED)},
    }


def bench_retrieval(base: Path, corpus: Path) -> dict:
    from koddi.context.retrieve import retrieve
    from koddi.policy.account import local_owner
    from koddi.projectmap import service

    root, project_id, _ = new_project(base, "corpus", source=corpus)
    started = time.monotonic()
    service.ensure(project_id, root, wait=300.0)
    initial = time.monotonic() - started
    target = next(root.rglob("*.py"))
    target.write_text(target.read_text(encoding="utf-8") + "\n# touched\n", encoding="utf-8")
    service.mark_dirty(project_id)
    started = time.monotonic()
    service.ensure(project_id, root, wait=120.0)
    incremental = time.monotonic() - started
    files = [path for path in root.rglob("*.py")]
    texts = {path.relative_to(root).as_posix(): path.read_text(encoding="utf-8", errors="replace").casefold() for path in files}
    rows, baseline_hits, upgraded_hits, chars = [], 0, 0, []
    for query, expected in QUERIES:
        words = [word for word in re.findall(r"[^\W_]+", query.casefold()) if len(word) >= 4]
        ranked = sorted(texts, key=lambda path: (-sum(texts[path].count(word) for word in words), path))[:5]
        result = retrieve(project_id, root, local_owner(), query, max_chars=12000, limit=12)
        paths = list(dict.fromkeys(item["path"] for item in result.get("items", [])))[:5]
        chars.append(sum(len(item.get("text", "")) for item in result.get("items", [])))
        found_baseline, found_upgraded = bool(set(expected) & set(ranked)), bool(set(expected) & set(paths))
        baseline_hits += found_baseline
        upgraded_hits += found_upgraded
        rows.append({"query": query, "expected": list(expected), "baseline_top5": found_baseline, "upgraded_top5": found_upgraded})
    return {"python_files_in_corpus": len(files), "index_seconds": {"initial": round(initial, 2), "incremental_one_file": round(incremental, 2)},
            "recall_at_5": {"baseline_term_count": round(baseline_hits / len(QUERIES), 3),
                            "upgraded_context_retrieve": round(upgraded_hits / len(QUERIES), 3)},
            "context_chars_per_query": {"median": sorted(chars)[len(chars) // 2], "max": max(chars),
                                        "baseline_whole_top5_files": sum(len(texts[path]) for path in ranked)},
            "queries": rows}


def bench_quality(base: Path) -> dict:
    from koddi.quality import gate

    rows, statuses = [], {}
    for index, (name, path, old, new) in enumerate(BUGS):
        root, project_id, _ = new_project(base, f"calc_{index}", CALC)
        from koddi.storage.db import db
        from koddi.tools.runner import ToolRunner

        with db() as conn:
            chat = conn.execute("INSERT INTO chats(title,project_id,mode,owner_id) VALUES ('bench',?,'development','__bench__')", (project_id,)).lastrowid
            turn = conn.execute("INSERT INTO turns(chat_id,status) VALUES (?,'running')", (chat,)).lastrowid
        runner = ToolRunner(root, project_id, turn)
        edited = runner.run("file.edit", {"path": path, "old_text": old, "new_text": new})
        baseline_caught = not all(check["ok"] for check in edited.get("checks", []))
        qa = runner.run("qa.run", {})
        decision = gate.evaluate(turn_id=turn, workspace=root, project_id=project_id, mode="development",
                                 tool_names={"qa.run"}, evidence=None, review_enabled=False)
        statuses[decision.state] = statuses.get(decision.state, 0) + 1
        rows.append({"bug": name, "baseline_caught": baseline_caught, "upgraded_caught": qa["status"] == "failed",
                     "qa": qa["summary"], "completion_state": decision.state})
    return {"seeded_regressions": len(BUGS), "caught": {"baseline_compile_check": sum(row["baseline_caught"] for row in rows),
                                                      "upgraded_qa_run": sum(row["upgraded_caught"] for row in rows)},
            "completion_decisions": statuses, "bugs": rows}


GRADE = "def grade(score):\n    if score >= 90:\n        return 'A'\n    if score > 50:\n        return 'B'\n    return 'C'\n"
WEAK_TEST = "from grade import grade\n\n\ndef test_a():\n    assert grade(95) == 'A'\n"
STRONG_TEST = WEAK_TEST + ("\n\ndef test_edges():\n    assert grade(90) == 'A'\n    assert grade(89) == 'B'\n"
                           "    assert grade(51) == 'B'\n    assert grade(50) == 'C'\n")
PRICE = ("def total_price(items, tax):\n    result = 0\n    for item in items:\n        if item.active:\n"
         "            result += item.price * item.count\n    result = result * (1 + tax)\n    return round(result, 2)\n")
LADDER = "def route(x):\n" + "".join(f"    if x == {n}:\n        return {n}\n" for n in range(12)) + "    return -1\n"
LAYERS = {"layers": [{"name": "domain", "paths": ["app/domain/**"]}, {"name": "infra", "paths": ["app/infra/**"]}]}
# фикстура: (имя, база в коммите, правка хода, гейты, политика слоёв, чего ждём от гейта)
UB_CASES = [
    ("small_clean_change", {"grade.py": GRADE, "tests/test_grade.py": STRONG_TEST}, {"grade.py": GRADE.replace("score", "value")},
     None, None, {"failed": []}),
    ("complex_function", {}, {"route.py": LADDER}, ["crap"], None, {"crap": "failed"}),
    ("renamed_duplicate", {"price.py": PRICE},
     {"order.py": PRICE.replace("total_price", "order_sum").replace("result", "acc").replace("item", "row")}, ["dry"], None,
     {"dry": "failed"}),
    ("weak_tests", {"grade.py": GRADE.replace("'C'", "'D'"), "tests/test_grade.py": WEAK_TEST}, {"grade.py": GRADE},
     ["mutation"], None, {"mutation": "failed"}),
    ("strong_tests", {"grade.py": GRADE.replace("'C'", "'D'"), "tests/test_grade.py": STRONG_TEST}, {"grade.py": GRADE},
     ["mutation"], None, {"mutation": "passed"}),
    ("layer_violation", {"app/__init__.py": "", "app/domain/__init__.py": "", "app/infra/__init__.py": "",
                         "app/infra/db.py": "X = 1\n", "app/domain/order.py": "Y = 2\n"},
     {"app/domain/order.py": "from app.infra import db\n\nY = db.X\n"}, ["architecture"], LAYERS, {"architecture": "failed"}),
    ("swallowed_error", {"svc.py": "def load(path):\n    return open(path).read()\n"},
     {"svc.py": "def load(path):\n    try:\n        return open(path).read()\n    except Exception:\n        pass\n"},
     ["signals"], None, {"signals": "failed"}),
    ("broken_test_baseline", {"grade.py": GRADE.replace("'C'", "'D'"), "tests/test_grade.py": WEAK_TEST + "\n\ndef test_x():\n    assert False\n"},
     {"grade.py": GRADE}, ["mutation"], None, {"mutation": "unavailable"}),
]
GATE_NUMBERS = ("violations", "unknown_coverage", "max_crap", "max_complexity", "candidates", "evaluated", "killed",
                "survived", "uncovered", "score", "new", "total", "high")


def bench_ub_gates(base: Path) -> dict:
    """Объективные гейты на посеянных фикстурах: что ловит прежняя проверка (тесты/линт) и что ловят гейты."""
    from koddi.quality import architecture, budgets, engine
    from koddi.storage.db import db
    from koddi.tools.runner import ToolRunner

    pytest_bin = shutil.which("pytest")
    rows = []
    for name, committed, change, gates, policy, expected in UB_CASES:
        files = {**CALC, **committed}
        if "grade.py" in committed:
            files.pop("tests/test_calc.py", None)
        root, project_id, _ = new_project(base, f"ub_{name}", files)
        for args in (["init", "-q"], ["add", "."], ["-c", "user.email=b@b", "-c", "user.name=b", "commit", "-qm", "init"]):
            subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)
        with db() as conn:
            chat = conn.execute("INSERT INTO chats(title,project_id,mode,owner_id) VALUES ('bench',?,'development','__bench__')",
                                (project_id,)).lastrowid
            turn = conn.execute("INSERT INTO turns(chat_id,status) VALUES (?,'running')", (chat,)).lastrowid
        runner = ToolRunner(root, project_id, turn)
        write_tree(root, change)
        if policy:
            architecture.set_policy(project_id, policy)
        # прежний сигнал готовности - проверки проекта (линт и тесты)
        baseline = runner.run("qa.run", {})
        budget = budgets.budget_for("light" if gates is None else "pro")
        started = time.monotonic()
        results = engine.run_gates(runner, gates=gates or budget["auto_gates"], paths=sorted(change), full=False, budget=budget,
                                   coverage_mode="none")
        elapsed = round((time.monotonic() - started) * 1000)
        statuses = {item.gate: item.status for item in results}
        if "failed" in expected:
            met = not [gate for gate, status in statuses.items() if status == "failed"]
        else:
            met = all(statuses.get(gate) == status for gate, status in expected.items())
        rows.append({
            "case": name, "expected": expected, "statuses": statuses, "met": met, "gates_ms": elapsed,
            "baseline_qa": baseline.get("status"),
            "numbers": {item.gate: {key: item.summary[key] for key in GATE_NUMBERS if item.summary.get(key) is not None}
                        for item in results},
        })
    seeded = [row for row in rows if any(status == "failed" for status in row["expected"].values())]
    return {"cases": len(rows), "expectations_met": sum(row["met"] for row in rows),
            "seeded_defects": len(seeded),
            "caught_by_baseline_qa": sum(row["baseline_qa"] == "failed" for row in seeded),
            "caught_by_gates": sum(row["met"] for row in seeded),
            "pytest": bool(pytest_bin), "rows": rows}


def bench_checkpoint(base: Path) -> dict:
    from koddi.changes.checkpoints import restore
    from koddi.policy.account import local_owner
    from koddi.storage.db import db
    from koddi.tools.runner import ToolRunner

    root, project_id, _ = new_project(base, "dirty", {"notes.py": "VALUE = 1\n", "calc.py": "X = 1\n"})
    for args in (["init", "-q"], ["add", "."], ["-c", "user.email=b@b", "-c", "user.name=b", "commit", "-qm", "init"]):
        subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)
    (root / "notes.py").write_text("VALUE = 2  # user\n", encoding="utf-8")
    (root / "draft.txt").write_text("user draft\n", encoding="utf-8")
    with db() as conn:
        chat = conn.execute("INSERT INTO chats(title,project_id,mode,owner_id) VALUES ('bench',?,'development','__bench__')", (project_id,)).lastrowid
        turn = conn.execute("INSERT INTO turns(chat_id,status) VALUES (?,'running')", (chat,)).lastrowid
    runner = ToolRunner(root, project_id, turn)
    runner.run("file.edit", {"path": "notes.py", "old_text": "VALUE = 2", "new_text": "VALUE = 3"})
    runner.run("file.edit", {"path": "calc.py", "old_text": "X = 1", "new_text": "X = 2"})
    (root / "calc.py").write_text("X = 99  # user after agent\n", encoding="utf-8")
    blocked = restore(turn, local_owner())
    done = restore(turn, local_owner(), ["notes.py"])
    return {"conflict_stops_restore": not blocked["restored"] and blocked.get("conflicts") == ["calc.py"],
            "user_dirty_file_restored_to_pre_turn": (root / "notes.py").read_text(encoding="utf-8") == "VALUE = 2  # user\n",
            "user_untracked_file_kept": (root / "draft.txt").exists(),
            "user_edit_after_agent_kept": "user after agent" in (root / "calc.py").read_text(encoding="utf-8"),
            "selective_restore_ok": bool(done.get("restored"))}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--corpus", type=Path, default=ROOT / "backend" / "koddi")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="dokky-bench-") as temporary:
        base = Path(temporary)
        os.environ["KODDI_DATA_DIR"] = str(base / "data")
        # инструменты проверок берутся из окружения, где запущен бенчмарк (ruff, pytest)
        os.environ["PATH"] = str(Path(sys.executable).parent) + os.pathsep + os.environ.get("PATH", "")
        import koddi.policy.account as account
        import koddi.policy.grants as grants
        import koddi.storage.db as storage

        storage.DB_PATH = base / "bench.sqlite3"
        # отдельный локальный владелец без входа в аккаунт и полный доступ только во временной папке
        account.local_owner = lambda: "__bench__"
        grants.permission_profile = lambda project_id=None, **_: "full"
        storage.init_db()
        started = time.monotonic()
        report = {
            "model_metrics": "not measured: no live model calls (tokens, cost, latency need a separate budgeted live run)",
            "symbols": bench_symbols(base),
            "retrieval": bench_retrieval(base, args.corpus.resolve()),
            "quality_gate": bench_quality(base),
            "checkpoint": bench_checkpoint(base),
            "ub_gates": bench_ub_gates(base),
        }
        report["seconds"] = round(time.monotonic() - started, 1)
        from koddi.projectmap import service

        service.shutdown()
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.out:
        args.out.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
