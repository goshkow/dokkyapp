from __future__ import annotations

import asyncio
import json

import pytest

import koddi.agents.telemetry as telemetry
import koddi.storage.db as storage
from koddi.agents.prompts import CONTEXT_PREFIX, coding_context_update, compose_coding_context


@pytest.mark.asyncio
@pytest.mark.parametrize("output", [{"content": "private result"}, {"tool_progress": {"name": "file.read"}}])
async def test_request_timing_separates_waiting_from_first_output_and_records_counts(monkeypatch, output):
    clock = [10.0]
    monkeypatch.setattr(telemetry, "monotonic", lambda: clock[0])
    metrics = telemetry.TurnMetrics(started=10.0)

    class Model:
        async def stream(self, **kwargs):
            telemetry.transport_attempt(retry=False)
            clock[0] = 12.0
            yield {"waiting_seconds": 2}
            telemetry.transport_attempt(retry=True)
            clock[0] = 15.0
            yield output
            clock[0] = 18.0
            yield {"usage": {"prompt_tokens": 1234, "completion_tokens": 50,
                             "completion_tokens_details": {"reasoning_tokens": 30}}}
            clock[0] = 20.0

    token = telemetry.current_metrics.set(metrics)
    try:
        _ = [chunk async for chunk in telemetry.ObservedClient(Model(), metrics).stream(
            messages=[{"role": "user", "content": "private prompt"}], tools=[],
        )]
    finally:
        telemetry.current_metrics.reset(token)
    record = metrics.snapshot()["request_timings"][0]
    assert record["status"] == "completed"
    assert record["wall_seconds"] == 10
    assert record["first_chunk_seconds"] == 2
    assert record["first_output_seconds"] == 5
    assert record["transport_attempts"] == 2 and record["retries"] == 1
    assert record["input_tokens"] == 1234 and record["reasoning_tokens"] == 30
    assert record["messages"] == 1 and record["tools"] == 0
    assert "private" not in json.dumps(record) and "file.read" not in json.dumps(record)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure,status", [(RuntimeError("private error"), "error"),
                                           (asyncio.CancelledError(), "cancelled")])
async def test_request_timing_keeps_errors_private_and_unknown_usage_unknown(failure, status):
    class Model:
        async def stream(self, **kwargs):
            raise failure
            yield

    metrics = telemetry.TurnMetrics()
    with pytest.raises(type(failure)):
        _ = [chunk async for chunk in telemetry.ObservedClient(Model(), metrics).stream()]
    record = metrics.request_timings[0]
    assert record["status"] == status
    assert record["first_output_seconds"] is None and record["input_tokens"] is None
    assert record["reasoning_tokens"] is None
    assert "private error" not in json.dumps(record)


@pytest.mark.asyncio
async def test_request_timing_history_is_bounded_and_replay_is_not_counted_as_new_usage():
    class Model:
        async def stream(self, **kwargs):
            yield {"usage": {"input_tokens": 100}, "dokky": {"replayed": True}}

    metrics = telemetry.TurnMetrics()
    client = telemetry.ObservedClient(Model(), metrics)
    for _ in range(telemetry.MAX_REQUEST_TIMINGS + 3):
        _ = [chunk async for chunk in client.stream()]
    assert len(metrics.request_timings) == telemetry.MAX_REQUEST_TIMINGS
    assert metrics.request_timings[0]["request"] == 4
    assert metrics.request_timings[-1]["input_tokens"] is None


def test_old_session_receives_execution_policy_as_one_context_delta(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DB_PATH", tmp_path / "timing.sqlite3")
    storage.init_db()
    current = compose_coding_context(mode="development", workspace=tmp_path, project_id=None, depth="pro")
    old = json.loads(current["content"][len(CONTEXT_PREFIX):])
    old.pop("execution_focus_policy")
    old.pop("plan_policy")
    pinned_system = {"role": "system", "content": "Old pinned system"}
    history = [pinned_system, {"role": "user", "content": CONTEXT_PREFIX + json.dumps(old)}]
    delta = coding_context_update(current, history)
    changes = json.loads(delta["content"][len(CONTEXT_PREFIX):])
    assert list(changes) == ["execution_focus_policy", "plan_policy"]
    assert "one or two" in changes["execution_focus_policy"]
    assert "plan.update" in changes["plan_policy"]
    assert old["work_depth"] == "pro" and history[0] == pinned_system
    assert coding_context_update(current, [*history, delta]) is None
