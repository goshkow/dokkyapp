from copy import deepcopy

import pytest

from koddi.dokky.client import DokkyError
from koddi.dokky.coding import compatible_tools
from koddi.tools.schemas import TOOL_SCHEMAS_BY_NAME, validate_tool_arguments


def tool(name, description="current"):
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {"type": "object", "properties": {}},
        },
    }


def test_existing_session_keeps_exact_order_and_schema_after_new_tool_added():
    pinned = [tool("file__read", "previous wording"), tool("web__fetch")]
    selected = compatible_tools(
        pinned, [tool("attachment.read"), tool("web.fetch"), tool("file.read")]
    )
    wire = [
        {
            "type": "function",
            "function": dict(item["function"], name=item["function"]["name"].replace(".", "__")),
        }
        for item in selected
    ]
    assert wire == pinned


def test_removed_or_changed_schema_is_explicitly_rejected():
    with pytest.raises(DokkyError):
        compatible_tools([tool("file__read")], [tool("file.create")])
    changed = tool("file.read")
    changed["function"]["parameters"]["properties"] = {"path": {"type": "string"}}
    with pytest.raises(DokkyError):
        compatible_tools([tool("file__read")], [changed])


def computer_tools():
    current = deepcopy(TOOL_SCHEMAS_BY_NAME["computer.use"])
    pinned = deepcopy(current)
    pinned["function"]["name"] = "computer__use"
    pinned["function"]["description"] = "Previous instructions"
    parameters = pinned["function"]["parameters"]
    for name in ("arguments", "working_directory", "button", "clicks", "query"):
        parameters["properties"].pop(name)
    parameters["properties"]["action"]["enum"].remove("list_windows")
    parameters["properties"]["action"]["enum"].remove("click_element")
    return pinned, current


def test_computer_optional_arguments_and_actions_keep_old_wire_schema():
    pinned, current = computer_tools()
    before = deepcopy([pinned, current])
    selected = compatible_tools([pinned], [current])
    selected[0]["function"]["name"] = "computer__use"
    assert selected[0]["function"] == pinned["function"]
    assert [pinned, current] == before
    assert validate_tool_arguments("computer.use", {"action": "launch", "application": "Calculator"}) is None
    assert validate_tool_arguments("computer.use", {"action": "arbitrary"}) is not None
    assert validate_tool_arguments("computer.use", {"action": "launch", "unexpected": "ignored?"}) is not None


@pytest.mark.parametrize("change", ["required", "type", "enum", "bound", "removed", "reference"])
def test_restrictive_or_unproven_schema_changes_are_rejected(change):
    pinned, current = computer_tools()
    schema = current["function"]["parameters"]
    if change == "required":
        schema["required"].append("pid")
    elif change == "type":
        schema["properties"]["pid"]["type"] = "string"
    elif change == "enum":
        schema["properties"]["action"]["enum"].remove("launch")
    elif change == "bound":
        schema["properties"]["text"]["maxLength"] = 100
    elif change == "removed":
        schema["properties"].pop("application")
    else:
        for item in (schema, pinned["function"]["parameters"]):
            item["allOf"] = [{"$ref": "#/properties/action"}]
    with pytest.raises(DokkyError) as caught:
        compatible_tools([pinned], [current])
    assert caught.value.code == "tools_changed"


def test_optional_addition_to_open_schema_can_restrict_previously_valid_arguments():
    pinned = tool("mcp__server__action")
    current = tool("mcp__server__action")
    current["function"]["parameters"]["properties"]["path"] = {"type": "string"}
    with pytest.raises(DokkyError):
        compatible_tools([pinned], [current])


def test_recursive_additions_keep_nested_closed_objects_and_array_items():
    pinned = tool("mcp__action")
    nested = {"type": "object", "additionalProperties": False, "required": ["path"],
              "properties": {"path": {"type": "string"}}}
    pinned["function"]["parameters"]["properties"]["edits"] = {"type": "array", "items": nested}
    current = deepcopy(pinned)
    current["function"]["parameters"]["properties"]["edits"]["items"]["properties"]["description"] = {"type": "string"}
    assert compatible_tools([pinned], [current])[0]["function"] == pinned["function"]


@pytest.mark.asyncio
@pytest.mark.parametrize("has_pending", [False, True])
async def test_recovered_session_is_checked_after_recovery_not_against_stale_cache(tmp_path, monkeypatch, has_pending):
    import koddi.agents.orchestrator as agent
    import koddi.storage.db as storage
    from koddi.dokky import coding

    monkeypatch.setattr(storage, "DB_PATH", tmp_path / "compatibility.sqlite3")
    storage.init_db()
    workspace = tmp_path / "project"
    workspace.mkdir()
    with storage.db() as conn:
        chat = conn.execute("INSERT INTO chats(title,mode) VALUES ('Old','development')").lastrowid
        conn.execute("INSERT INTO messages(chat_id,role,content) VALUES (?,'user','Earlier')", (chat,))
    prefix = [{"role": "system", "content": "Pinned instructions"},
              {"role": "assistant", "content": "Previously completed"}]
    pinned, _ = computer_tools()
    recovered = {"session": {"id": "cs_" + "a" * 24, "reasoning_mode": "pro"},
                 "messages": prefix, "tools": [pinned], "history": None}
    stale = dict(recovered, tools=[tool("removed__stale")], _recover=True)
    pending_request = {"key": "original-key", "body": {"tools": [pinned]},
                       "messages": [*prefix, {"role": "user", "content": "Прерванная задача"}]}
    original_pending = deepcopy(pending_request)
    state = {"recovered": False, "requests": [], "pending": pending_request if has_pending else None}

    async def ensure(_chat):
        state["recovered"] = True
        return recovered

    async def discover():
        return [], {}, []

    class Model:
        config = {"provider": "dokky", "model": "dokky-code", "context_tokens": 200000}

        async def stream(self, **request):
            state["requests"].append(deepcopy(request))
            state["pending"] = None
            yield {"content": "Готово.", "finish_reason": "stop"}

    monkeypatch.setattr(coding, "ensure", ensure)
    monkeypatch.setattr(coding, "load", lambda _: recovered if state["recovered"] else stale)
    monkeypatch.setattr(coding, "pending", lambda _: state["pending"])
    monkeypatch.setattr(agent, "discover_agent_tools", discover)
    monkeypatch.setattr(agent, "retrieve_memory", lambda *_: [])
    monkeypatch.setattr(agent, "remote_config", lambda: Model.config)
    events = [event async for event in agent.AgentOrchestrator(Model()).run(
        chat_id=chat, project_id=None, workspace=workspace, user_message="Следующая задача",
    )]
    assert "turn.completed" in events[-1]
    assert len(state["requests"]) == (2 if has_pending else 1)
    assert state["requests"][0]["messages"][:len(prefix)] == prefix
    if has_pending:
        assert state["requests"][0]["messages"] == original_pending["messages"]
        assert pending_request == original_pending
        assert sum(message.get("content") == "Следующая задача" for message in state["requests"][1]["messages"]) == 1
    actual = state["requests"][0]["tools"]
    actual[0]["function"]["name"] = "computer__use"
    assert actual[0]["function"] == pinned["function"]


def test_sessions_pinned_before_code_intelligence_upgrade_stay_compatible():
    # снимок схем всех инструментов до семантических инструментов кода и QA: старые диалоги не должны ломаться
    import json
    from pathlib import Path

    from koddi.agents.orchestrator import CONTROL_TOOLS
    from koddi.tools.schemas import TOOL_SCHEMAS

    pinned = json.loads((Path(__file__).parent / "fixtures" / "pinned_tool_schemas_v1.json").read_text(encoding="utf-8"))
    selected = compatible_tools(pinned, [*TOOL_SCHEMAS, *CONTROL_TOOLS])
    assert [item["function"]["parameters"] for item in selected] == [item["function"]["parameters"] for item in pinned]
