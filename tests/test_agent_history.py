import pytest

from koddi.agents.history import checkpoint, replay_history, repair_history, compacted_messages


def test_history_repairs_missing_result_without_repeating_call():
    history = [
        {"role": "user", "content": "task"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [{"id": "a", "function": {"name": "file.create", "arguments": "{}"}}],
        },
        {"role": "user", "content": "continue"},
    ]
    repaired = repair_history(history)
    assert repaired[2]["role"] == "tool"
    assert repaired[2]["tool_call_id"] == "a"
    assert "interrupted" in repaired[2]["content"]
    assert repaired[3]["role"] == "user"


def test_checkpoint_replaces_context_not_audit():
    records = [
        {"role": "user", "content": "old"},
        checkpoint([{"role": "user", "content": "summary"}], "compaction"),
        {"role": "assistant", "content": "new"},
    ]
    assert [m["content"] for m in replay_history(records)] == ["summary", "new"]
    assert records[0]["content"] == "old"


def test_compaction_cannot_replace_context_with_empty_summary():
    with pytest.raises(ValueError):
        compacted_messages(
            [{"role": "system", "content": "instructions"}, {"role": "user", "content": "task"}],
            "",
            {"role": "user", "content": "task"},
            [],
        )


def test_compaction_keeps_current_user_once_and_after_previous_answer():
    user = {"role": "user", "content": "Current request"}
    system = {"role": "system", "content": "Full instructions"}
    old = {"role": "user", "content": "Old context " * 1000}
    recent = {"role": "assistant", "content": "Actual recent result"}
    compacted = compacted_messages([system, old, recent, user], "Useful summary", user, [])
    assert compacted[0] == system
    assert compacted[-1] == user
    assert sum(item == user for item in compacted) == 1
    assert recent in compacted


def test_oversized_exchange_is_not_split_into_orphan_tool_messages():
    user = {"role": "user", "content": "Current request"}
    system = {"role": "system", "content": "Full instructions"}
    call = {
        "role": "assistant",
        "content": None,
        "tool_calls": [{"id": "a", "function": {"name": "file.read", "arguments": "{}"}}],
    }
    result = {"role": "tool", "tool_call_id": "a", "content": "huge output " * 10000}
    source = [system, user, call, result]
    compacted = compacted_messages(
        source, "Read file.py; test failed with exit code 1", user, [], max_chars=2000
    )
    assert compacted[0] == system and compacted[-1] == user
    assert not any(item.get("role") == "tool" or item.get("tool_calls") for item in compacted)
    assert "test failed with exit code 1" in compacted[1]["content"]
    assert result["content"] == "huge output " * 10000


def test_current_exchange_and_continuation_are_preserved_when_they_fit():
    user = {"role": "user", "content": "Current task"}
    call = {"role": "assistant", "content": "Partial output", "tool_calls": [{"id": "a"}]}
    result = {"role": "tool", "tool_call_id": "a", "content": "Actual result"}
    continuation = {"role": "user", "content": "Continue the unfinished task"}
    source = [
        {"role": "system", "content": "instructions"},
        user,
        {"role": "assistant", "content": "old" * 2000},
        call,
        result,
        continuation,
    ]
    compacted = compacted_messages(source, "Checkpoint", user, [], max_chars=2000)
    assert compacted[-3:] == [call, result, continuation]
    assert sum(item == user for item in compacted) == 1
