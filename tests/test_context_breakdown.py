import json

from koddi.agents.breakdown import context_breakdown
from koddi.agents.prompts import CONTEXT_PREFIX


def _context(**extra):
    value = {
        "mode": "development",
        "project_instructions": "## AGENTS.md (scope: .)\nправила\n\n## sub/CLAUDE.md (scope: sub)\nещё",
        "memory": [{"content": "abc"}],
        "user_instructions": "отвечай кратко",
        "environment": {"os": "macOS"},
    }
    value.update(extra)
    return {"role": "user", "content": CONTEXT_PREFIX + json.dumps(value, ensure_ascii=False)}


def _tool(name, description="d"):
    return {"type": "function", "function": {"name": name, "description": description, "parameters": {}}}


def _rows(result):
    return {row["key"]: row for row in result["rows"]}


def test_categories_are_separated():
    messages = [
        {"role": "system", "content": "s" * 3000},
        _context(),
        {"role": "user", "content": "привет"},
        {"role": "assistant", "content": "готово"},
        {"role": "tool", "content": "r" * 600},
    ]
    tools = [_tool("fs.read"), _tool("mcp_abc", "MCP github/search: поиск")]
    result = context_breakdown(messages, tools, window=200_000, compact_at=150_000)
    rows = _rows(result)
    assert rows["system"]["tokens"] >= 1000
    assert [item["name"] for item in rows["memory"]["items"]] >= ["AGENTS.md"]
    assert {item["name"] for item in rows["memory"]["items"]} == {"AGENTS.md", "sub/CLAUDE.md", "Память проекта (1)", "Личные инструкции"}
    assert rows["mcp"]["items"][0]["name"] == "github/search"
    assert rows["tools"]["tokens"] > 0 and rows["messages"]["tokens"] > 0 and rows["results"]["tokens"] >= 200
    # окружение не включает файлы памяти повторно
    assert rows["environment"]["tokens"] < rows["memory"]["tokens"] + 40


def test_reserve_and_free_fill_the_window():
    result = context_breakdown([{"role": "user", "content": "x" * 3000}], [], window=100_000, compact_at=80_000)
    rows = _rows(result)
    assert rows["reserve"]["tokens"] == 20_000
    assert rows["free"]["tokens"] == 100_000 - result["used"] - 20_000
    assert result["used"] + rows["reserve"]["tokens"] + rows["free"]["tokens"] == 100_000


def test_overflow_never_goes_negative():
    result = context_breakdown([{"role": "user", "content": "x" * 3_000_000}], [], window=100_000, compact_at=80_000)
    rows = _rows(result)
    assert rows["free"]["tokens"] == 0
    assert result["window"] >= result["used"]


def test_broken_context_message_is_counted_as_environment():
    broken = {"role": "user", "content": CONTEXT_PREFIX + "{не json"}
    result = context_breakdown([broken], [], window=10_000, compact_at=8_000)
    assert _rows(result)["environment"]["tokens"] > 0
