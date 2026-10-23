from pathlib import Path
from koddi.tools import tool_schemas_for_mode


def test_development_mode_exposes_workspace_toolchain() -> None:
    names = {item["function"]["name"] for item in tool_schemas_for_mode("development")}
    assert {"file.create", "file.write_patch", "test.run", "shell.run", "git.diff"} <= names


def test_chat_mode_exposes_no_workspace_tools() -> None:
    assert tool_schemas_for_mode("chat") == []


def test_development_prompt_explains_real_file_workflow() -> None:
    prompt = Path("prompts/modes/development.ru.txt").read_text(encoding="utf-8")
    assert "create or edit real files with tools" in prompt


def test_chat_titles_are_not_mapped_from_example_phrases() -> None:
    source = Path("backend/koddi/agents/orchestrator.py").read_text(encoding="utf-8")
    assert "Скрипт приветствия" not in source
    assert "Сортировка пузырьком" not in source


def test_agent_never_advertises_unimplemented_or_metadata_only_execution():
    from koddi.tools.schemas import AGENT_UNAVAILABLE_TOOLS

    for mode in ("development", "debug", "plan", "review"):
        names = {item["function"]["name"] for item in tool_schemas_for_mode(mode)}
        assert not names & AGENT_UNAVAILABLE_TOOLS
