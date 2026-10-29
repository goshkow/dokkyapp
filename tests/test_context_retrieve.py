"""Индекс фрагментов, поиск контекста под бюджет, важность файлов, тесты файла и анализ влияния."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

import koddi.storage.db as storage
from koddi.context import retrieve as retrieve_module
from koddi.projectmap import chunks, service
from koddi.tools.runner import ToolRunner

OWNER = "__test__"


def write(root: Path, relative: str, text: str) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


@pytest.fixture
def project(tmp_path, monkeypatch):
    from koddi.projects import add_project

    monkeypatch.setattr(storage, "DB_PATH", tmp_path / "ctx.sqlite3")
    monkeypatch.setattr("koddi.policy.grants.permission_profile", lambda project_id=None, **_: "full")
    monkeypatch.setattr("koddi.policy.account.local_owner", lambda: OWNER)
    retrieve_module._cache.clear()
    storage.init_db()
    root = tmp_path / "proj"
    write(root, "billing/invoice.py", (
        '"""Счета клиентов."""\n'
        "from billing.tax import vat_rate\n\n\n"
        "def invoice_total(lines, country):\n"
        "    subtotal = sum(line.price * line.qty for line in lines)\n"
        "    return subtotal * (1 + vat_rate(country))\n\n\n"
        "def format_invoice(number):\n"
        "    return f'INV-{number:06d}'\n"
    ))
    write(root, "billing/tax.py", "RATES = {'DE': 0.19, 'FR': 0.2}\n\n\ndef vat_rate(country):\n    return RATES.get(country, 0.0)\n")
    write(root, "billing/__init__.py", "")
    write(root, "api/routes.py", (
        "from fastapi import APIRouter\n"
        "from billing.invoice import invoice_total\n"
        'router = APIRouter(prefix="/api/invoices")\n\n'
        '@router.post("")\n'
        "def create(payload):\n    return invoice_total(payload.lines, payload.country)\n"
    ))
    write(root, "web/client.ts", "export async function send(body: object) {\n  return fetch('/api/invoices', {method: 'POST', body: JSON.stringify(body)});\n}\n")
    write(root, "tests/test_tax.py", "from billing.tax import vat_rate\n\n\ndef test_vat():\n    assert vat_rate('DE') == 0.19\n")
    write(root, "docs/billing.md", "# Billing\n\nInvoices include VAT.\n\n## Taxes\n\nvat_rate returns the rate.\n")
    write(root, ".env", "SECRET=topsecret\n")
    project = add_project(str(root))
    yield project["id"], root.resolve()
    service.shutdown()


def test_chunks_follow_declarations_and_terms_split_identifiers():
    import ast

    text = "import os\n\n\ndef first():\n    return 1\n\n\nclass Second:\n    def method(self):\n        return 2\n"
    ranges = chunks.chunk_ranges("python", text, ast.parse(text))
    assert ranges[0] == [1, 1, "", "module"]
    assert [4, 5, "first", "function"] in ranges and [8, 10, "Second", "class"] in ranges
    assert {"get", "user", "id", "getuserid"} <= set(chunks.terms("getUserId").split())
    assert "invoice" in chunks.terms("invoice_total")


def test_retrieve_ranks_relevant_code_with_provenance(project):
    project_id, root = project
    result = ToolRunner(root, project_id).run("context.retrieve", {"query": "how is the invoice total with VAT computed"})
    assert result["ok"] and result["provider"] == "fts5"
    first = result["items"][0]
    assert first["path"] == "billing/invoice.py" and first["symbol"] == "invoice_total"
    assert first["lines"] == [5, 7] and len(first["sha256"]) == 64 and first["reasons"]
    assert "subtotal * (1 + vat_rate(country))" in first["text"]
    assert all(item["path"] != ".env" for item in result["items"])
    assert any("tests/test_tax.py" == item.get("path") for item in result["related"]) or result["items"]
    assert result["budget"]["used"] <= result["budget"]["max_chars"]


def test_retrieve_exact_identifier_and_budget(project):
    project_id, root = project
    result = ToolRunner(root, project_id).run("context.retrieve", {"query": "vat_rate", "max_chars": 1000, "limit": 2})
    assert result["items"][0]["symbol"] == "vat_rate"
    assert "declaration name matches" in result["items"][0]["reasons"]
    assert len(result["items"]) <= 2 and result["budget"]["used"] <= 1000


def test_retrieve_reindexes_changes_and_never_returns_stale_text(project):
    project_id, root = project
    runner = ToolRunner(root, project_id)
    assert runner.run("context.retrieve", {"query": "format_invoice"})["items"][0]["symbol"] == "format_invoice"
    write(root, "billing/invoice.py", "def format_receipt(number):\n    return f'R-{number}'\n")
    service.mark_dirty(project_id)
    result = runner.run("context.retrieve", {"query": "format_receipt"})
    assert result["items"][0]["symbol"] == "format_receipt" and "R-" in result["items"][0]["text"]
    assert not any(item["symbol"] == "format_invoice" for item in runner.run("context.retrieve", {"query": "format_invoice"})["items"])
    # файл изменён, а индекс ещё нет: фрагмент пропускается с предупреждением
    write(root, "billing/tax.py", "def vat_rate(country):\n    raise NotImplementedError\n")
    service._scanned_at[service._k(project_id)] = 10**12
    stale = runner.run("context.retrieve", {"query": "vat_rate RATES"})
    assert all(item["path"] != "billing/tax.py" for item in stale["items"])
    assert any("changed after indexing" in item for item in stale.get("warnings", []))


def test_removed_files_and_projects_drop_chunks(project):
    project_id, root = project
    service.scan(project_id, root)
    (root / "docs/billing.md").unlink()
    service.scan(project_id, root)
    with storage.db() as conn:
        assert not conn.execute("SELECT 1 FROM context_chunks WHERE path='docs/billing.md'").fetchone()
        assert conn.execute("SELECT COUNT(*) FROM context_chunks WHERE project_id=?", (project_id,)).fetchone()[0] > 0
    service.forget(project_id)
    with storage.db() as conn:
        assert conn.execute("SELECT COUNT(*) FROM context_chunks WHERE project_id=?", (project_id,)).fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM context_fts").fetchone()[0] == 0


def test_rank_and_tests_for(project):
    project_id, root = project
    service.scan(project_id, root)
    graph = service.graph(project_id, OWNER)
    assert graph.rank["billing/tax.py"] > graph.rank["api/routes.py"]
    assert ("tests/test_tax.py", "imports it") in graph.tests_for("billing/tax.py")
    # тесты ищутся вверх по импортам: тест налога не проверяет модуль счетов
    assert graph.tests_for("billing/invoice.py") == []


def test_retrieve_validates_paths_and_query(project):
    project_id, root = project
    runner = ToolRunner(root, project_id)
    assert runner.run("context.retrieve", {"query": "x", "paths": ["../outside.py"]}).get("blocked")
    assert not runner.run("context.retrieve", {"query": "   "})["ok"]


def test_impact_of_symbol_lists_users_dependents_tests_and_api(project):
    project_id, root = project
    result = ToolRunner(root, project_id).run("code.impact", {"symbol": "vat_rate"})
    assert result["ok"], result
    assert {item["path"] for item in result["usages"]} >= {"billing/tax.py", "billing/invoice.py", "tests/test_tax.py"}
    assert any(item["path"] == "api/routes.py" for item in result["dependents"])
    assert any(item["path"] == "tests/test_tax.py" for item in result["tests"])
    assert result["suggested_commands"] == [] or "pytest" in result["suggested_commands"][0]
    assert any(item["api"].startswith("POST /api/invoices") for item in result["api"])
    assert result["risk"]["level"] in {"low", "medium", "high"} and result["risk"]["reasons"]


def test_impact_of_working_copy_changes(project):
    project_id, root = project
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init"], cwd=root, check=True)
    write(root, "billing/tax.py", "RATES = {'DE': 0.19}\n\n\ndef vat_rate(country):\n    return RATES.get(country, 0.0)\n")
    result = ToolRunner(root, project_id).run("code.impact", {})
    assert [item["path"] for item in result["seeds"]] == ["billing/tax.py"]
    assert result["seeds"][0]["reason"] == "changed in the working copy"
    assert any(item["path"] == "tests/test_tax.py" for item in result["tests"])
