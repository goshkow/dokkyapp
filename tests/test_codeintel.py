"""Семантические инструменты кода: символы, ссылки, переименование, извлечение и правка символа."""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import pytest

from koddi.storage.db import init_db
from koddi.tools.runner import ToolRunner


@pytest.fixture(autouse=True)
def _db():
    init_db()


def write(root: Path, files: dict[str, str]) -> None:
    for relative, text in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode("utf-8"))


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setattr("koddi.policy.grants.permission_profile", lambda project_id=None, **_: "full")
    write(tmp_path, {
        "pkg/__init__.py": "from .models import Cart, Order\n__all__ = ['Cart', 'Order']\n",
        "pkg/models.py": (
            "class Cart:\n"
            "    def total(self):\n"
            "        return 1\n\n\n"
            "class Order:\n"
            "    def total(self):\n"
            "        return 2\n\n\n"
            "class BigCart(Cart):\n"
            "    def total(self):\n"
            "        return super().total() + 1\n"
        ),
        "app.py": (
            "from pkg import Cart, Order\n\n\n"
            "def checkout(cart: Cart, order: Order):\n"
            "    # total() в комментарии\n"
            "    label = 'total'\n"
            "    return cart.total() + order.total()\n\n\n"
            "def loose(thing):\n"
            "    return thing.total()\n"
        ),
    })
    return tmp_path


def run(root: Path, name: str, args: dict) -> dict:
    return ToolRunner(root).run(name, args)


def test_references_separate_same_name_methods(project):
    result = run(project, "code.references", {"symbol": "Cart.total"})
    assert result["ok"] and result["provider"] == "python_ast" and result["accuracy"] == "verified"
    places = {(item["path"], item["line"]) for item in result["references"]}
    assert ("app.py", 7) in places and ("pkg/models.py", 2) in places
    # метод Order.total и упоминания в комментарии и строке ссылками не считаются
    assert ("pkg/models.py", 7) not in places
    assert not any(item["path"] == "app.py" and item["line"] in {5, 6} for item in result["references"])
    assert any(item["path"] == "app.py" and item["line"] == 11 for item in result["candidates"])


def test_ambiguous_name_lists_candidates(project):
    result = run(project, "code.references", {"symbol": "total"})
    assert not result["ok"] and "ambiguous" in result["error"]
    assert "pkg/models.py:2" in result["error"] and "pkg/models.py:7" in result["error"]


def test_definition_follows_imports_and_position(project):
    result = run(project, "code.definition", {"path": "app.py", "line": 7, "column": 17})
    assert result["ok"] and result["definitions"][0]["path"] == "pkg/models.py"
    assert result["definitions"][0]["line"] == 2
    by_name = run(project, "code.definition", {"symbol": "Order"})
    assert by_name["definitions"][0]["line"] == 6


def test_outline_and_symbol_search_are_semantic(project):
    outline = run(project, "code.outline", {"path": "pkg/models.py"})
    assert outline["provider"] == "python_ast"
    assert [item["name"] for item in outline["symbols"] if item["kind"] == "class"] == ["Cart", "Order", "BigCart"]
    found = run(project, "symbol.search", {"query": "Cart"})
    assert found["symbols"][0]["name"] == "Cart" and found["symbols"][0]["exact"]


def test_rename_needs_decision_on_unknown_receivers_then_renames_family(project):
    pending = run(project, "refactor.rename", {"old": "pkg/models.py:Cart.total", "new": "grand_total"})
    assert not pending["ok"] and pending["error_code"] == "needs_review"
    assert "grand_total" not in (project / "pkg/models.py").read_text()

    preview = run(project, "refactor.rename", {"old": "pkg/models.py:Cart.total", "new": "grand_total",
                                               "include_candidates": False, "preview": True})
    assert preview["ok"] and "+    def grand_total(self):" in preview["diff"]
    assert "grand_total" not in (project / "pkg/models.py").read_text()

    done = run(project, "refactor.rename", {"old": "pkg/models.py:Cart.total", "new": "grand_total",
                                            "include_candidates": False})
    assert done["ok"] and done["accuracy"] == "verified" and done["change_set_id"]
    models = (project / "pkg/models.py").read_text()
    app = (project / "app.py").read_text()
    # переопределение в наследнике и super() переименованы, метод другого класса - нет
    assert models.count("def grand_total") == 2 and "super().grand_total()" in models
    assert "class Order:\n    def total(self)" in models
    assert "cart.grand_total() + order.total()" in app
    assert "# total() в комментарии" in app and "'total'" in app and "thing.total()" in app


def test_rename_class_updates_imports_and_all(project):
    result = run(project, "refactor.rename", {"old": "Cart", "new": "Basket", "glob": "**/*"})
    assert result["ok"], result
    assert "from .models import Basket, Order" in (project / "pkg/__init__.py").read_text()
    assert "__all__ = ['Basket', 'Order']" in (project / "pkg/__init__.py").read_text()
    assert "def checkout(cart: Basket, order: Order)" in (project / "app.py").read_text()
    assert "class BigCart(Basket)" in (project / "pkg/models.py").read_text()
    rollback = ToolRunner(project).rollback_changes(result["change_set_id"])
    assert rollback.get("ok") and "class Cart:" in (project / "pkg/models.py").read_text()


def test_rename_refuses_text_and_capture(project):
    text = run(project, "refactor.rename", {"old": "return 1", "new": "x", "glob": "**/*"})
    assert not text["ok"] and "file.search_replace" in text["error"]
    write(project, {"pkg/extra.py": "def helper():\n    value = 1\n    other = 2\n    return value + other\n"})
    capture = run(project, "refactor.rename", {"old": "pkg/extra.py:2:5", "new": "other"})
    assert not capture["ok"]
    assert (project / "pkg/extra.py").read_text().startswith("def helper():\n    value = 1")


def test_extract_function_computes_inputs_and_outputs(project):
    write(project, {"calc.py": (
        "def report(items, factor):\n"
        "    total = 0\n"
        "    for item in items:\n"
        "        total += item * factor\n"
        "    print(total)\n"
        "    return total\n"
    )})
    result = run(project, "refactor.extract", {"path": "calc.py", "selection": "2-4", "name": "sum_items"})
    assert result["ok"], result
    text = (project / "calc.py").read_text()
    assert "def sum_items(items, factor):" in text and "total = sum_items(items, factor)" in text
    namespace: dict = {}
    exec(compile(text, "calc.py", "exec"), namespace)
    assert namespace["report"]([1, 2], 3) == 9


def test_extract_refuses_return_inside_selection(project):
    write(project, {"calc.py": "def f(x):\n    if x:\n        return 1\n    return 2\n"})
    result = run(project, "refactor.extract", {"path": "calc.py", "selection": "2-3"})
    assert not result["ok"] and "return" in result["error"]


def test_symbol_edit_reindents_checks_syntax_and_deletes(project):
    result = run(project, "code.symbol_edit", {"symbol": "Order.total", "new_text": "def total(self):\n    return 20\n"})
    assert result["ok"], result
    assert "class Order:\n    def total(self):\n        return 20\n" in (project / "pkg/models.py").read_text()
    broken = run(project, "code.symbol_edit", {"symbol": "Order.total", "new_text": "def total(self:\n"})
    assert not broken["ok"] and "does not parse" in broken["error"]
    stale = run(project, "code.symbol_edit", {"symbol": "Order.total", "new_text": "def total(self): pass",
                                              "expected_text": "def total(self):\n    return 2"})
    assert not stale["ok"] and "expected_text" in stale["error"]
    removed = run(project, "code.symbol_edit", {"symbol": "loose", "new_text": ""})
    assert removed["ok"] and "def loose" not in (project / "app.py").read_text()


def test_crlf_and_bom_are_preserved(project):
    (project / "win.py").write_bytes("\ufeffdef old_name():\r\n    return 1\r\n\r\nold_name()\r\n".encode("utf-8"))
    result = run(project, "refactor.rename", {"old": "win.py:old_name", "new": "new_name"})
    assert result["ok"], result
    assert (project / "win.py").read_bytes() == "\ufeffdef new_name():\r\n    return 1\r\n\r\nnew_name()\r\n".encode("utf-8")


def test_paths_outside_project_are_blocked(project):
    result = run(project, "code.definition", {"path": "../outside.py", "line": 1})
    assert not result["ok"] and result.get("blocked")


def test_read_only_profile_blocks_semantic_writes(project, monkeypatch):
    monkeypatch.setattr("koddi.policy.grants.permission_profile", lambda project_id=None, **_: "read-only")
    result = run(project, "refactor.rename", {"old": "Order", "new": "Purchase"})
    assert not result["ok"] and result.get("blocked")
    assert run(project, "code.references", {"symbol": "Order"})["ok"]


# ---------- TypeScript

def test_typescript_without_language_service_is_marked_approximate(project):
    write(project, {
        "web/util.ts": "export function price(v: number) { return v * 2; }\n// price в комментарии\nconst s = 'price';\n",
        "web/main.ts": "import { price } from './util';\nconsole.log(price(1));\n",
    })
    result = run(project, "code.references", {"symbol": "price"})
    assert result["ok"] and result["provider"] == "text" and result["accuracy"] == "approximate"
    assert {(item["path"], item["line"]) for item in result["references"]} == {
        ("web/util.ts", 1), ("web/main.ts", 1), ("web/main.ts", 2)}
    rename = run(project, "refactor.rename", {"old": "price", "new": "cost"})
    assert not rename["ok"] and "unavailable" in rename["error"]
    assert "price" in (project / "web/util.ts").read_text()


def test_typescript_7_native_reports_missing_api(project):
    from koddi.codeintel import typescript as tsl

    write(project, {"node_modules/typescript/package.json": json.dumps({"name": "typescript", "version": "7.0.2"}),
                    "src/a.ts": "export const a = 1;\n"})
    library, reason = tsl._find_typescript(project, "src/a.ts")
    assert library is None and "TypeScript 7" in reason


def _typescript_package() -> Path | None:
    configured = os.environ.get("KODDI_TEST_TYPESCRIPT")
    candidates = [Path(configured)] if configured else []
    candidates.append(Path(__file__).resolve().parents[1] / "frontend" / "node_modules" / "typescript")
    for candidate in candidates:
        if (candidate / "lib" / "typescript.js").is_file():
            return candidate
    return None


@pytest.fixture
def ts_project(project):
    package = _typescript_package()
    if package is None or shutil.which("node") is None:
        pytest.skip("TypeScript 5.x and node are needed (set KODDI_TEST_TYPESCRIPT to a typescript package)")
    shutil.copytree(package, project / "node_modules" / "typescript")
    write(project, {
        "tsconfig.json": json.dumps({"compilerOptions": {"strict": True, "target": "ES2022", "module": "ESNext",
                                                         "moduleResolution": "Bundler", "noEmit": True},
                                     "include": ["web"]}),
        "web/util.ts": "export function price(v: number): number {\n  const doubled = v * 2;\n  return doubled + 1;\n}\n"
                       "export class Shop { price = 3; }\n",
        "web/main.ts": "import { price, Shop } from './util';\n// price\nconsole.log(price(1), new Shop().price, 'price');\n",
    })
    yield project
    from koddi.codeintel import typescript as tsl

    tsl.shutdown()


def test_typescript_references_rename_and_type_rollback(ts_project):
    refs = run(ts_project, "code.references", {"symbol": "web/util.ts:1:17"})
    assert refs["ok"] and refs["provider"] == "typescript"
    assert {(item["path"], item["line"]) for item in refs["references"]} == {
        ("web/util.ts", 1), ("web/main.ts", 1), ("web/main.ts", 3)}
    done = run(ts_project, "refactor.rename", {"old": "web/util.ts:1:17", "new": "cost"})
    assert done["ok"], done
    main = (ts_project / "web/main.ts").read_text()
    assert "import { cost, Shop }" in main and "cost(1), new Shop().price, 'price'" in main and "// price" in main
    # правка тела с ошибкой типов показывается, а не откатывается
    edited = run(ts_project, "code.symbol_edit", {"symbol": "web/util.ts:cost",
                                                  "new_text": "export function cost(v: number): number {\n  return 'x';\n}"})
    assert edited["ok"] and edited["typecheck"]["new_errors"]


def test_typescript_extract_uses_requested_name(ts_project):
    result = run(ts_project, "refactor.extract", {"path": "web/util.ts", "selection": "2-2", "name": "twice"})
    assert result["ok"], result
    text = (ts_project / "web/util.ts").read_text()
    assert "function twice(" in text and "newFunction" not in text


def test_typescript_language_service_needs_trusted_profile(ts_project, monkeypatch):
    monkeypatch.setattr("koddi.policy.grants.permission_profile", lambda project_id=None, **_: "ask")
    refs = run(ts_project, "code.references", {"symbol": "web/util.ts:1:17"})
    assert refs["ok"] and refs["provider"] == "text"
    assert any("permission mode" in item for item in refs["warnings"])


def test_worker_rejects_files_outside_root(ts_project):
    from koddi.codeintel import typescript as tsl

    worker = tsl.worker(ts_project, "web/util.ts", None)
    with pytest.raises(ValueError, match="outside"):
        worker.call("symbols", {"file": "../../etc/hosts"})
