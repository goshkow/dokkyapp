from __future__ import annotations

import asyncio
import json
import threading
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from time import monotonic
from typing import Any

from koddi.agents.progress import FailureTracker, local_title
from koddi.agents.telemetry import ObservedClient, TurnMetrics, current_metrics
from koddi.agents.history import (
    CHECKPOINT,
    checkpoint,
    compacted_messages,
    initial_instructions,
    repair_history,
    replay_history,
)
from koddi.agents.compaction import summarize_history, context_limit, context_size, needs_compaction
from koddi.agents.prompts import (
    coding_context_update,
    compose_coding_context,
    compose_coding_system_prompt,
    compose_system_prompt,
)
from koddi.agents.mcp_tools import discover_agent_tools
from koddi.agents.memory import retrieve_memory
from koddi.logs import log_event as audit_event
from koddi.policy.describe import describe_action
from koddi.policy.grants import permission_profile as access_profile
from koddi.policy.instructions import format_instructions
from koddi.policy.review import review_action
from koddi.agents.recovery import current_owner
from koddi.remote import RemoteAIClient, remote_config
from koddi.remote.client import ContextLimitError, RemoteAIError
from koddi.storage.db import db, get_setting, durable_write
from koddi.tools import TOOL_SCHEMAS, ToolRunner, tool_schemas_for_mode
from koddi.tools.output_store import save_tool_output
from koddi.tools.runner import tool_output_excerpt


def sse(event: str, data: Any) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"


async def responsive_stream(iterator, cancel_event: threading.Event | None = None):
    pending = None
    started = monotonic()
    last_heartbeat = started
    try:
        while True:
            if cancel_event is not None and cancel_event.is_set():
                raise asyncio.CancelledError
            if pending is None:
                pending = asyncio.create_task(anext(iterator))
            done, _ = await asyncio.wait({pending}, timeout=0.2)
            if not done:
                if monotonic() - last_heartbeat >= 1:
                    last_heartbeat = monotonic()
                    yield {"waiting_seconds": max(1, round(monotonic() - started))}
                continue
            try:
                chunk = pending.result()
            except StopAsyncIteration:
                return
            pending = None
            started = monotonic()
            last_heartbeat = started
            yield chunk
    finally:
        if pending is not None and not pending.done():
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)
        await iterator.aclose()


async def execute_tool(runner, name, args, cancel_event, *, approved=False):
    pending = asyncio.create_task(asyncio.to_thread(runner.run, name, args, approved=approved))
    try:
        result = await asyncio.shield(pending)
    except asyncio.CancelledError:
        cancel_event.set()
        # синхронные записи не оборвать, новый ход не начинаем, пока они не закончатся
        await pending
        raise
    if cancel_event.is_set() or result.get("cancelled"):
        raise asyncio.CancelledError
    return result


async def execute_tool_events(runner, name, args, cancel_event, *, approved=False):
    pending = asyncio.create_task(execute_tool(runner, name, args, cancel_event, approved=approved))
    started = monotonic()
    last_heartbeat = started
    try:
        while not pending.done():
            await asyncio.wait({pending}, timeout=0.2)
            if not pending.done() and monotonic() - last_heartbeat >= 1:
                last_heartbeat = monotonic()
                yield {"waiting_seconds": round(monotonic() - started)}
        yield {"result": pending.result()}
    finally:
        if not pending.done():
            pending.cancel()
        await asyncio.gather(pending, return_exceptions=True)


def control_tool(
    name: str, description: str, properties: dict, required: list | None = None
) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": list(properties) if required is None else required,
                "additionalProperties": False,
            },
        },
    }


CONTROL_TOOLS = [
    control_tool(
        "plan.update",
        "Maintain implementation steps. In plan mode also publish the complete Markdown implementation contract in document, not a brief checklist.",
        {
            "document": {
                "type": "string",
                "description": "Full Markdown plan: goal, scope, design, affected files, ordered implementation, acceptance criteria, tests, risks and open questions. Use the user's language.",
            },
            "steps": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "status": {
                            "type": "string",
                            "enum": ["pending", "running", "completed", "blocked"],
                        },
                    },
                    "required": ["title", "status"],
                    "additionalProperties": False,
                },
            },
        },
        required=["steps"],
    ),
    control_tool(
        "task.complete",
        "Finish after verification. Report files, checks, limitations and run instructions.",
        {
            "summary": {
                "type": "string",
                "description": "Final report in Markdown for the user's language: one outcome sentence, then short ## sections (what changed, checks, what remains) with lists, `code` for paths and commands, **bold** for key facts. No walls of text.",
            }
        },
    ),
    control_tool(
        "task.ask_user",
        "Ask for missing information and wait for a human response.",
        {"question": {"type": "string"}},
    ),
    control_tool(
        "task.delegate",
        "Delegate a self-contained subtask to another agent. Wait for its verified result. Child actions are serial to avoid conflicting workspace writes.",
        {
            "title": {"type": "string"},
            "task": {"type": "string"},
            "mode": {"type": "string", "enum": ["plan", "development", "debug", "review"]},
        },
    ),
]


def journal(chat_id: int, message: dict) -> None:
    with db() as conn:
        conn.execute(
            "INSERT INTO agent_history(chat_id,payload_json) VALUES (?,?)",
            (chat_id, json.dumps(message, ensure_ascii=False)),
        )


def create_message(chat_id: int, kind: str, turn_id: int | None = None) -> int:
    with db() as conn:
        return int(
            conn.execute(
                "INSERT INTO messages(chat_id,role,content,metadata_json) VALUES (?,'assistant','',?)",
                (
                    chat_id,
                    json.dumps(
                        {
                            "status": "running",
                            "kind": kind,
                            "turn_id": turn_id,
                            "events": [],
                            "started_at": datetime.now(timezone.utc).isoformat(
                                timespec="milliseconds"
                            ),
                        }
                    ),
                ),
            ).lastrowid
        )


def save_message(message_id: int, text: str, metadata: dict):
    if metadata.get("status") in {"completed", "truncated", "failed", "cancelled", "superseded"}:
        metadata = {
            **metadata,
            "completed_at": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
        }
    with db() as conn:
        row = conn.execute(
            "SELECT metadata_json FROM messages WHERE id=?", (message_id,)
        ).fetchone()
        previous = json.loads(row[0] or "{}") if row else {}
        conn.execute(
            "UPDATE messages SET content=?,metadata_json=? WHERE id=?",
            (text, json.dumps(previous | metadata, ensure_ascii=False), message_id),
        )


class AgentOrchestrator:
    """Цикл инструментов с удалённой моделью: история провайдера и история интерфейса хранятся отдельно."""

    def __init__(self, client: RemoteAIClient | None = None, *, _depth: int = 0):
        self.client = client
        self._depth = _depth

    async def run(
        self,
        *,
        chat_id: int,
        project_id: int | None,
        workspace: Path | None,
        user_message: str,
        display_message: str | None = None,
        attachment_ids: list[int] | None = None,
        queued_request_id: str | None = None,
        mode: str = "development",
        model: str | None = None,
        options: dict | None = None,
        cancel_event: threading.Event | None = None,
        origin: str | None = None,
    ):
        cancel_event = cancel_event or threading.Event()
        config = remote_config() | getattr(self.client, "config", {})
        options = dict(options or {})
        options.pop("_context_checkpoint", None)
        if config.get("provider") == "dokky":
            options["_chat_id"] = chat_id
        client = self.client or RemoteAIClient(config)
        selected = model or get_setting("selected_model", config["model"]) or config["model"]
        if config.get("provider") == "dokky":
            selected = "dokky-code"
        if config.get("provider") == "dokky-test" and model is None:
            selected = config["model"] or "dokky-test-scripted"
        mode = "development" if mode == "build" else mode
        with db() as conn:
            first = not conn.execute(
                "SELECT 1 FROM messages WHERE chat_id=? AND role='user'", (chat_id,)
            ).fetchone()
            user_id = int(
                conn.execute(
                    "INSERT INTO messages(chat_id,role,content) VALUES (?,'user',?)",
                    (chat_id, display_message if display_message is not None else user_message),
                ).lastrowid
            )
            turn_id = int(
                conn.execute(
                    "INSERT INTO turns(chat_id,status,owner_pid,owner_token,queued_request_id,history_from) "
                    "VALUES (?,'running',?,?,?,(SELECT COALESCE(MAX(id),0) FROM agent_history WHERE chat_id=?))",
                    (chat_id, *current_owner(), queued_request_id, chat_id),
                ).lastrowid
            )
            conn.execute(
                "UPDATE messages SET metadata_json=? WHERE id=?",
                (json.dumps({"turn_id": turn_id, "attachment_ids": attachment_ids or [], **({"origin": origin} if origin else {})}), user_id),
            )
            # чат с новой задачей поднимается в списке и показывает свежее время, а не время создания
            conn.execute("UPDATE chats SET updated_at=CURRENT_TIMESTAMP WHERE id=?", (chat_id,))
            records = list(
                conn.execute(
                    "SELECT payload_json FROM agent_history WHERE chat_id=? ORDER BY id", (chat_id,)
                )
            )
            previous_plan = conn.execute(
                "SELECT steps_json FROM agent_plans JOIN turns ON turns.id=agent_plans.turn_id "
                "WHERE turns.chat_id=? ORDER BY turns.id DESC LIMIT 1",
                (chat_id,),
            ).fetchone()
        metrics = TurnMetrics(turn_id=turn_id)
        metric_token = current_metrics.set(metrics)
        client = ObservedClient(client, metrics)
        failures = FailureTracker()
        plan: list[dict] = []
        created_ids: list[int] = []

        async def wait_input(kind: str, payload: dict):
            with db() as conn:
                request_id = int(
                    conn.execute(
                        "INSERT INTO agent_requests(turn_id,kind,payload_json) VALUES (?,?,?)",
                        (turn_id, kind, json.dumps(payload, ensure_ascii=False)),
                    ).lastrowid
                )
            yield sse(
                "agent.input_required",
                {**payload, "call_id": payload.get("id"), "id": request_id, "kind": kind},
            )
            # поток хода могут не читать (очередь, переподключение окна): окно узнаёт о запросе из общей шины
            from koddi.events import event_bus

            await event_bus.publish("agent.request.created", {"chat_id": chat_id, "request_id": request_id, "kind": kind})
            while True:
                if cancel_event.is_set():
                    raise asyncio.CancelledError
                with db() as conn:
                    row = conn.execute(
                        "SELECT response_json FROM agent_requests WHERE id=?", (request_id,)
                    ).fetchone()
                if row is None:
                    raise RuntimeError("Запрос ввода больше не существует")
                if row[0] is not None:
                    answer = json.loads(row[0])
                    if not isinstance(answer, dict):
                        raise ValueError("Ответ пользователя должен быть объектом")
                    yield answer
                    return
                await asyncio.sleep(0.2)

        try:
            if cancel_event.is_set():
                raise asyncio.CancelledError
            yield sse(
                "turn.started",
                {
                    "turn_id": turn_id,
                    "model": selected,
                    "mode": mode,
                    "provider": config.get("provider", "openai-compatible"),
                    "test_provider": config.get("provider") == "dokky-test",
                },
            )
            yield sse("agent.status", {"text": "Думаю…"})
            audit = [json.loads(row[0]) for row in records]
            history = replay_history(audit)
            repaired = history if config.get("provider") == "dokky" else repair_history(history)
            if repaired != history:
                await durable_write(journal, chat_id, checkpoint(repaired, "protocol_repair"))
            user = {"role": "user", "content": user_message}
            if config.get("provider") != "dokky":
                await durable_write(journal, chat_id, user)
            if previous_plan and mode != "chat":
                previous = json.loads(previous_plan[0])
                if any(s.get("status") in {"pending", "running"} for s in previous):
                    plan = previous
            allowed_names = (
                {t["function"]["name"] for t in tool_schemas_for_mode(mode)} if workspace else set()
            )
            allowed_names.update(
                {"web.search", "web.fetch", "tool.output_read", "attachment.read"}
                if mode != "chat"
                else set()
            )
            allowed_names.update(
                t["function"]["name"]
                for t in CONTROL_TOOLS
                if t["function"]["name"] != "task.delegate" or mode in {"development", "debug"}
            )
            schema_mode = "development" if config.get("provider") == "dokky" else mode
            tools = list(tool_schemas_for_mode(schema_mode)) if workspace else []
            if mode != "chat":
                present = {item["function"]["name"] for item in tools}
                tools += [
                    item
                    for item in TOOL_SCHEMAS
                    if item["function"]["name"]
                    in {"web.search", "web.fetch", "tool.output_read", "attachment.read"} - present
                ]
                controls = [
                    t
                    for t in CONTROL_TOOLS
                    if t["function"]["name"] != "task.delegate"
                    or (
                        (config.get("provider") == "dokky" or mode in {"development", "debug"})
                        and self._depth < 4
                    )
                ]
                tools = [t for t in tools if t["function"]["name"] != "task.ask_user"] + controls
            mcp_bindings = {}
            if workspace and (
                mode in {"development", "debug"}
                or (config.get("provider") == "dokky" and mode in {"plan", "review"})
            ):
                from koddi.integrations.mcp import mcp_config_path
                from koddi.policy.grants import permission_profile

                configured = mcp_config_path().is_file()
                connect_allowed = not configured or permission_profile(project_id) in {"trusted", "full"}
                if not connect_allowed:
                    answer = {}
                    async for value in wait_input(
                        "approval",
                        {
                            "name": "mcp.tools_list",
                            "arguments": {},
                            "question": "Подключить настроенные MCP-серверы? Это может запустить внешние программы.",
                        },
                    ):
                        if isinstance(value, str):
                            yield value
                        else:
                            answer = value
                    connect_allowed = answer.get("approved") is True
                yield sse("agent.status", {"text": "Подключение внешних инструментов"})

                async def discover():
                    return await discover_agent_tools() if connect_allowed else ([], {}, [])

                discovery = asyncio.create_task(discover())
                try:
                    async with asyncio.timeout(15):
                        while not discovery.done():
                            if cancel_event.is_set():
                                raise asyncio.CancelledError
                            await asyncio.wait({discovery}, timeout=0.2)
                        mcp_schemas, mcp_bindings, mcp_errors = discovery.result()
                        tools.extend(mcp_schemas)
                        if mode in {"development", "debug"}:
                            allowed_names.update(mcp_bindings)
                        if mcp_errors:
                            yield sse(
                                "agent.warning",
                                {
                                    "error": "Часть MCP-инструментов недоступна; подробности в логах."
                                },
                            )
                            from koddi.logs import log_event

                            await durable_write(
                                log_event,
                                "WARNING",
                                "mcp.discovery",
                                "MCP discovery errors",
                                {"errors": mcp_errors},
                            )
                except TimeoutError:
                    yield sse(
                        "agent.warning",
                        {
                            "error": "Подключение MCP заняло слишком долго; продолжу со встроенными инструментами."
                        },
                    )
                finally:
                    if not discovery.done():
                        discovery.cancel()
                    await asyncio.gather(discovery, return_exceptions=True)
            if config.get("provider") == "dokky":
                from koddi.dokky.coding import load, compatible_tools

                tools.sort(key=lambda item: item["function"]["name"])
            memory = await asyncio.to_thread(retrieve_memory, project_id, user_message)
            current_context = None
            context_checkpoint = False
            depth = "light"
            if config.get("provider") == "dokky":
                from koddi.dokky.coding import turn_mode

                depth = turn_mode(chat_id)
                system = compose_coding_system_prompt()
                current_context = compose_coding_context(
                    project_id=project_id, mode=mode, workspace=workspace, memory=memory, plan=plan, depth=depth,
                )
            else:
                system, _, _ = compose_system_prompt(
                    project_id=project_id,
                    mode=mode,
                    task=user_message,
                    workspace=workspace,
                    include_tools=bool(tools),
                    tool_schemas=tools,
                    memory=memory,
                )
            messages = [{"role": "system", "content": system}, *repaired, user]
            pending_followup = None
            if config.get("provider") == "dokky":
                from koddi.dokky.coding import (
                    ensure, recover, pending, unconfirmed_tail, journal_since_recovery,
                )

                canonical = await ensure(chat_id)
                from koddi.dokky.context import should_compact, transition

                if not canonical["messages"] and canonical["session"].get("history", {}).get("length", 0):
                    canonical = await recover(chat_id)
                # сначала восстановление и проверка, затем платная смена режима или сжатие
                compatible_tools(canonical["tools"], tools)
                unresolved_tools = pending(chat_id)
                if unresolved_tools:
                    compatible_tools(unresolved_tools["body"].get("tools", []), tools)
                if canonical["session"].get("pending_reasoning_mode") and not pending(chat_id):
                    # выбранный режим вступает в силу только с этим сообщением, не раньше
                    from koddi.dokky.coding import apply_pending_mode

                    async for event in responsive_stream(
                        apply_pending_mode(chat_id, config=config, runtime_context=current_context),
                        cancel_event,
                    ):
                        if "event" in event:
                            yield sse(event["event"], {k: v for k, v in event.items() if k != "event"})
                    canonical = await ensure(chat_id)
                if not pending(chat_id) and should_compact(canonical, config, boundary=True):
                    async for event in responsive_stream(transition(chat_id, config=config, runtime_context=current_context), cancel_event):
                        if "event" in event:
                            yield sse(event["event"], {k: v for k, v in event.items() if k != "event"})
                    canonical = await ensure(chat_id)
                if not canonical["messages"] and canonical["session"].get("history", {}).get(
                    "length", 0
                ):
                    canonical = await recover(chat_id)
                unresolved_tools = pending(chat_id)
                if unresolved_tools:
                    tools = compatible_tools(unresolved_tools["body"].get("tools", []), tools)
                elif not canonical["tools"] and not canonical["messages"]:
                    from koddi.tools.schemas import tool_schemas_for_new_session
                    from koddi.policy.account import local_owner
                    from koddi.computer_use.service import consent_enabled

                    tools = tool_schemas_for_new_session(
                        tools,
                        runtime_extensions=get_setting("coding_runtime_extensions:" + local_owner(), False) is True,
                        computer_use=consent_enabled(),
                    )
                tools = compatible_tools(canonical["tools"], tools)
                tools = [{"type": item["type"], "function": deepcopy(item["function"])} for item in tools]
                prefix = [{"role": "system", "content": system}, *deepcopy(repaired)]
                tail = []
                if canonical["messages"]:
                    prefix = deepcopy(canonical["messages"])
                    recovered_journal = journal_since_recovery(chat_id, canonical, repaired)
                    tail = unconfirmed_tail(canonical["messages"], recovered_journal)
                    if canonical["session"].get("journal_rebase_after") is not None and not recovered_journal:
                        yield sse("agent.warning", {
                            "error": "История сверена с сервером. Конфликтующий локальный хвост сохранён в аудите и не отправляется повторно."
                        })
                    last = prefix[-1] if prefix else {}
                    if last.get("tool_calls"):
                        results = []
                        for call in last["tool_calls"]:
                            result = next(
                                (m for m in tail if m.get("tool_call_id") == call["id"]), None
                            )
                            results.append(
                                result
                                or {
                                    "role": "tool",
                                    "tool_call_id": call["id"],
                                    "content": json.dumps(
                                        {
                                            "ok": False,
                                            "interrupted": True,
                                            "error": "Previous execution was interrupted; inspect the workspace before retrying.",
                                        }
                                    ),
                                }
                            )
                        ids = {call["id"] for call in last["tool_calls"]}
                        tail = results + [m for m in tail if m.get("tool_call_id") not in ids]
                saved_checkpoint = next((record for record in reversed(audit)
                                         if record.get(CHECKPOINT) == 1 and record.get("reason") == "compaction"), None)
                coding_base = saved_checkpoint.get("coding_base", {}) if saved_checkpoint else {}
                if (
                    canonical.get("history") is not None
                    and coding_base.get("session_id") == canonical["session"]["id"]
                    and coding_base.get("history") == canonical["history"]
                    and coding_base.get("journal_rebase_after") == canonical["session"].get("journal_rebase_after")
                ):
                    # сжатие записано, но новый префикс ещё не принят сервером
                    prefix = [deepcopy(canonical["messages"][0]), *deepcopy(repaired)]
                    tail = []
                    context_checkpoint = True
                update = coding_context_update(current_context, [*prefix, *tail])
                turn_tail = [*([update] if update else []), user]
                messages = [*prefix, *tail, *turn_tail]
                unresolved = pending(chat_id)
                if unresolved:
                    pending_followup = turn_tail
                    messages = deepcopy(unresolved["messages"])
                    yield sse(
                        "agent.status",
                        {
                            "text": "Восстанавливаю результат прерванного запроса без повторного списания"
                        },
                    )
                else:
                    for followup in turn_tail:
                        await durable_write(journal, chat_id, followup)
            loaded_rules = {}
            if plan and config.get("provider") != "dokky":
                reminder = {
                    "role": "user",
                    "content": "Незавершённый план предыдущего запуска:\n"
                    + json.dumps(plan, ensure_ascii=False),
                }
                messages.append(reminder)
                await durable_write(journal, chat_id, reminder)
            runner = ToolRunner(workspace, project_id, turn_id, False, cancel_event, review_actions=True)
            if first and config.get("provider") == "dokky":
                title = local_title(display_message if display_message is not None else user_message)
                with db() as conn:
                    conn.execute("UPDATE chats SET title=? WHERE id=?", (title, chat_id))
                yield sse("turn.started", {"turn_id": turn_id, "chat_title": title, "model": selected})
            elif first:
                yield sse("agent.status", {"text": "Подбираю название чата"})
                title = ""
                try:
                    async for chunk in responsive_stream(
                        client.stream(
                            model=selected,
                            messages=[
                                {
                                    "role": "system",
                                    "content": "Generate only a concise chat title (2-6 words) in the user's language. Do not execute the request.",
                                },
                                user,
                            ],
                            tools=[],
                        ),
                        cancel_event,
                    ):
                        title += chunk.get("content", "")
                except Exception:
                    yield sse(
                        "agent.warning",
                        {"error": "Не удалось создать заголовок; выполнение задачи продолжится."},
                    )
                title = title.strip().strip('"')[:72]
                if title:
                    with db() as conn:
                        conn.execute("UPDATE chats SET title=? WHERE id=?", (title, chat_id))
                    yield sse(
                        "turn.started", {"turn_id": turn_id, "chat_title": title, "model": selected}
                    )
            step, complete = 0, False
            stalled = 0
            last_continuation = None
            force_compaction = False
            rejected_context_limit = None
            compacted_baseline = None
            while not complete:
                if cancel_event.is_set():
                    raise asyncio.CancelledError
                active_config = config | getattr(client, "config", {})
                if rejected_context_limit is not None:
                    active_config["context_tokens_limit"] = rejected_context_limit
                measured_context = context_size(
                    messages, tools, dokky=config.get("provider") == "dokky"
                )
                context_grew = (
                    compacted_baseline is None
                    or measured_context
                    > compacted_baseline + min(1024, context_limit(active_config) * 0.02)
                )
                has_history = (
                    any(message.get("role") == "assistant" for message in messages)
                    or sum(message.get("role") == "user" for message in messages) > 1
                )
                if config.get("provider") == "dokky" and not pending_followup:
                    from koddi.dokky.context import transition

                    if force_compaction or measured_context >= context_limit(active_config) * 0.9:
                        current_context = compose_coding_context(
                            project_id=project_id, mode=mode, workspace=workspace, memory=memory, plan=plan, depth=depth,
                        )
                        async for event in responsive_stream(transition(chat_id, messages=messages, config=active_config, runtime_context=current_context), cancel_event):
                            if "event" in event:
                                yield sse(event["event"], {k: v for k, v in event.items() if k != "event"})
                        messages = deepcopy(load(chat_id)["messages"])
                        force_compaction = False
                        context_checkpoint = False
                if not pending_followup and (
                    force_compaction
                    or has_history
                    and context_grew
                    and needs_compaction(messages, tools, active_config)
                ) and config.get("provider") != "dokky":
                    yield sse("agent.status", {"text": "Сжимаю контекст"})
                    limit = context_limit(active_config)
                    divisor = 3 if config.get("provider") == "dokky" else 4
                    tool_chars = len(json.dumps(tools, ensure_ascii=False))
                    fixed = [
                        *initial_instructions(messages),
                        user,
                        {"role": "user", "content": json.dumps(plan, ensure_ascii=False)},
                    ]
                    if current_context is not None:
                        current_context = compose_coding_context(
                            project_id=project_id, mode=mode, workspace=workspace,
                            memory=memory, plan=plan, depth=depth,
                        )
                        fixed.append(current_context)
                    latest_developer = next(
                        (
                            message
                            for message in reversed(messages)
                            if message.get("role") == "developer"
                        ),
                        None,
                    )
                    if latest_developer and latest_developer not in fixed:
                        fixed.append(latest_developer)
                    fixed_chars = len(json.dumps(fixed, ensure_ascii=False)) + 512
                    max_chars = max(
                        int(limit * divisor * 0.3) - tool_chars,
                        fixed_chars + min(4096, int(limit * divisor * 0.08)),
                    )
                    max_chars = min(max_chars, int(limit * divisor * 0.9) - tool_chars)
                    if max_chars < fixed_chars + 256:
                        required = (fixed_chars + tool_chars) // divisor
                        raise ValueError(
                            f"Обязательные инструкции и задача занимают около {required} токенов из {limit}; для сжатия не осталось места. История сохранена"
                        )
                    summary = ""
                    async for chunk in summarize_history(
                        client,
                        model=selected,
                        messages=messages,
                        char_budget=int(limit * divisor * 0.72),
                        target_chars=max(256, min(max_chars // 3, max_chars - fixed_chars)),
                        cancel_event=cancel_event,
                        responsive=responsive_stream,
                    ):
                        if "part" in chunk:
                            yield sse(
                                "agent.status",
                                {
                                    "text": f"Сжимаю контекст: часть {chunk['part']} из {chunk['parts']}"
                                },
                            )
                        if "waiting_seconds" in chunk:
                            yield sse("agent.waiting", {"seconds": chunk["waiting_seconds"]})
                        if "summary" in chunk:
                            summary = chunk["summary"]
                    compacted = compacted_messages(
                        messages,
                        summary,
                        user,
                        plan,
                        max_chars=max_chars,
                        preserve_terminal_exchange=config.get("provider") == "dokky",
                        current_context=current_context,
                    )
                    if (
                        context_size(compacted, tools, dokky=config.get("provider") == "dokky")
                        > limit * 0.9 + 2
                    ):
                        raise ValueError(
                            "Обязательный контекст не помещается в окно модели; история сохранена"
                        )
                    saved_checkpoint = checkpoint(compacted[1:], "compaction")
                    if config.get("provider") == "dokky":
                        value = load(chat_id) or canonical
                        saved_checkpoint["coding_base"] = {
                            "session_id": value["session"]["id"],
                            "history": deepcopy(value.get("history")),
                            "journal_rebase_after": value["session"].get("journal_rebase_after"),
                        }
                    await durable_write(journal, chat_id, saved_checkpoint)
                    messages = compacted
                    context_checkpoint = config.get("provider") == "dokky"
                    force_compaction = False
                    compacted_baseline = context_size(
                        messages, tools, dokky=config.get("provider") == "dokky"
                    )
                    target_met = (
                        context_size(messages, tools, dokky=config.get("provider") == "dokky")
                        <= limit * 0.3 + 2
                    )
                    yield sse(
                        "context.compacted",
                        {
                            "text": "Контекст сжат, продолжаю задачу"
                            if target_met
                            else "Контекст сжат; обязательные инструкции сохранены целиком",
                            "context_tokens": context_size(
                                messages, tools, dokky=config.get("provider") == "dokky"
                            ),
                            "context_limit": limit,
                            "target_met": target_met,
                        },
                    )
                step += 1
                metrics.loop_cycles = step
                yield sse("agent.step", {"step": step})
                text, calls, reason = "", [], None
                message_id = await durable_write(create_message, chat_id, "text", turn_id)
                created_ids.append(message_id)
                last_flush = 0.0
                stream_options = dict(options)
                if context_checkpoint:
                    stream_options["_context_checkpoint"] = True
                try:
                    async for chunk in responsive_stream(
                        client.stream(
                            model=selected, messages=messages, tools=tools, options=stream_options
                        ),
                        cancel_event,
                    ):
                        if cancel_event.is_set():
                            raise asyncio.CancelledError
                        if "waiting_seconds" in chunk:
                            yield sse("agent.waiting", {"seconds": chunk["waiting_seconds"]})
                        if chunk.get("reset"):
                            text, calls, reason = "", [], None
                            await durable_write(save_message, message_id, "", {"status": "running"})
                            yield sse(
                                "message.reset", {"message_id": message_id, "turn_id": turn_id}
                            )
                        if "usage" in chunk:
                            yield sse(
                                "agent.usage",
                                {"usage": chunk["usage"], "dokky": chunk.get("dokky", {})},
                            )
                        if "content" in chunk:
                            text += chunk["content"]
                            yield sse(
                                "message.delta",
                                {
                                    "content": chunk["content"],
                                    "message_id": message_id,
                                    "turn_id": turn_id,
                                    "offset": len(
                                        text[: -len(chunk["content"])].encode("utf-16-le")
                                    )
                                    // 2,
                                },
                            )
                            if monotonic() - last_flush > 0.25:
                                await durable_write(
                                    save_message, message_id, text, {"status": "running"}
                                )
                                last_flush = monotonic()
                        if "tool_progress" in chunk:
                            progress = dict(chunk["tool_progress"])
                            if progress.get("name") in mcp_bindings:
                                progress["display_name"] = "/".join(mcp_bindings[progress["name"]])
                            yield sse("tool.progress", {**progress, "message_id": message_id})
                        if "tool_call" in chunk:
                            calls.append(chunk["tool_call"])
                        reason = chunk.get("finish_reason", reason)
                except ContextLimitError as exc:
                    if text or calls or pending_followup:
                        raise
                    rejected_context_limit = (
                        min(context_limit(active_config), exc.limit)
                        if exc.limit and exc.limit >= 4096
                        else context_limit(active_config)
                    )
                    force_compaction = True
                    await durable_write(
                        save_message,
                        message_id,
                        "",
                        {"status": "superseded", "kind": "context_retry"},
                    )
                    continue
                if cancel_event.is_set():
                    raise asyncio.CancelledError
                if reason == "length":
                    calls = []
                call_ids = [c.get("id") for c in calls]
                if any(not isinstance(i, str) or not i for i in call_ids) or len(
                    set(call_ids)
                ) != len(call_ids):
                    raise ValueError("AI-сервер прислал некорректные идентификаторы вызовов")
                metrics.tool_calls += len(calls)
                if calls:
                    await durable_write(metrics.persist, turn_id)
                if not text and not calls and reason != "length":
                    raise ValueError("AI-сервер вернул пустой ответ без действий")
                if not calls and mode in {"development", "debug", "build"} and getattr(runner, "completion_diagnostics", lambda: [])():
                    raise RemoteAIError("В изменённых файлах остались ошибки синтаксиса; результат и диагностика сохранены", code="verification_failed")
                context_checkpoint = False
                channel = (
                    "commentary"
                    if calls
                    or reason == "length"
                    or (
                        mode != "plan"
                        and any(item.get("status") in {"pending", "running"} for item in plan)
                    )
                    else "final"
                )
                await durable_write(
                    save_message,
                    message_id,
                    text,
                    {
                        "status": "truncated" if reason == "length" else "completed",
                        "channel": channel,
                    },
                )
                yield sse(
                    "message.updated",
                    {"message_id": message_id, "turn_id": turn_id, "channel": channel},
                )
                assistant: dict[str, Any] = {"role": "assistant", "content": text or None}
                if calls:
                    assistant["tool_calls"] = [
                        {
                            "id": c["id"],
                            "type": "function",
                            "function": {"name": c["name"], "arguments": c["arguments"]},
                        }
                        for c in calls
                    ]
                messages.append(assistant)
                await durable_write(journal, chat_id, assistant)
                if not calls:
                    if pending_followup:
                        for followup in pending_followup:
                            messages.append(followup)
                            await durable_write(journal, chat_id, followup)
                        pending_followup = None
                        continue
                    if reason == "length":
                        if config.get("provider") == "dokky":
                            raise RemoteAIError(
                                "Достигнут лимит ответа. Частичный текст сохранён; для продолжения отправьте новое сообщение. Обрезанные вызовы инструментов не исполнялись.",
                                code="response_truncated",
                            )
                        continuation = {
                            "role": "user",
                            "content": "Продолжите с места остановки: достигнут лимит ответа сервера, задача не завершена. Обрезанные вызовы инструментов не исполнялись; при необходимости вызовите их заново.",
                        }
                        messages.append(continuation)
                        await durable_write(journal, chat_id, continuation)
                    elif mode != "plan" and any(
                        item.get("status") in {"pending", "running"} for item in plan
                    ):
                        if config.get("provider") == "dokky":
                            raise ValueError(
                                "Модель завершила ответ, но в плане остались незавершённые шаги. История и план сохранены; продолжение требует сообщения пользователя."
                            )
                        continuation = {
                            "role": "user",
                            "content": "В текущем плане остались незавершённые шаги. Продолжите задачу или отметьте действительно заблокированные шаги и объясните ограничение.",
                        }
                        messages.append(continuation)
                        await durable_write(journal, chat_id, continuation)
                    else:
                        complete = True
                        break
                    signature = (text, reason)
                    stalled = stalled + 1 if signature == last_continuation else 1
                    last_continuation = signature
                    if stalled >= 3:
                        raise ValueError(
                            "Продолжение повторяется без новых действий; задача не завершена"
                        )
                    if reason == "length" or plan:
                        continue
                stalled, last_continuation = 0, None
                activity_id = await durable_write(create_message, chat_id, "activity", turn_id)
                created_ids.append(activity_id)
                events = []
                completion_summary = None
                for call in calls:
                    if cancel_event.is_set():
                        raise asyncio.CancelledError
                    name = call["name"]
                    try:
                        args = json.loads(call["arguments"])
                        if not isinstance(args, dict):
                            raise ValueError("arguments must be an object")
                    except (ValueError, TypeError):
                        args = None
                    data = {
                        "name": name,
                        "arguments": args or {},
                        "id": call["id"],
                        "message_id": activity_id,
                        "turn_id": turn_id,
                        "created_at": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
                    }
                    if name in mcp_bindings:
                        data["display_name"] = "/".join(mcp_bindings[name])
                    events.append({"event": "tool.call", "data": data})
                    await durable_write(
                        save_message, activity_id, "", {"status": "running", "events": events}
                    )
                    yield sse("tool.call", data)
                    tool_elapsed = 0.0
                    if pending_followup:
                        result = {"ok": False, "interrupted": True, "skipped": True,
                                  "error": "Восстановлен ответ прежнего запроса. Действие не исполнялось; сначала учтите новое сообщение пользователя и проверьте текущее состояние."}
                    elif complete:
                        result = {
                            "ok": False,
                            "skipped": True,
                            "error": "Задача уже завершена; последующие действия не исполнялись.",
                        }
                    elif args is None:
                        result = {
                            "ok": False,
                            "error": "Некорректные JSON-аргументы. Повторите с обязательными параметрами.",
                        }
                    elif (
                        name not in {t["function"]["name"] for t in tools}
                        or name not in allowed_names
                    ):
                        result = {"ok": False, "error": "Инструмент недоступен в активном режиме."}
                    elif self._control_error(name, args):
                        result = {"ok": False, "error": self._control_error(name, args)}
                    elif name == "plan.update":
                        proposed = args.get("steps", [])
                        valid = isinstance(proposed, list) and all(
                            isinstance(s, dict)
                            and s.get("title")
                            and s.get("status") in {"pending", "running", "completed", "blocked"}
                            for s in proposed
                        )
                        missing_document = (
                            mode == "plan" and not str(args.get("document") or "").strip()
                        )
                        valid = valid and not missing_document
                        result = (
                            {"ok": valid, "steps": proposed}
                            if valid
                            else {
                                "ok": False,
                                "error": (
                                    "В режиме планирования передайте document: полный Markdown-контракт реализации, а не только список шагов."
                                    if missing_document
                                    else "Некорректные шаги плана"
                                ),
                            }
                        )
                        # документ плана (plan.md) существует только в режиме планирования: в разработке модель
                        # иногда присылает его сама, хотя её просили искать или исправлять, а не планировать
                        document = str(args.get("document") or "") if mode == "plan" else ""
                        if valid:
                            plan = proposed
                            with db() as conn:
                                conn.execute(
                                    "INSERT INTO agent_plans(turn_id,steps_json,document_markdown) VALUES (?,?,?) ON CONFLICT(turn_id) DO UPDATE SET steps_json=excluded.steps_json,document_markdown=CASE WHEN excluded.document_markdown<>'' THEN excluded.document_markdown ELSE agent_plans.document_markdown END",
                                    (
                                        turn_id,
                                        json.dumps(plan, ensure_ascii=False),
                                        document,
                                    ),
                                )
                            result["document"] = document
                            yield sse("plan.updated", result)
                    elif name == "task.ask_user":
                        result = {}
                        async for value in wait_input("question", args):
                            if isinstance(value, str):
                                yield value
                            else:
                                result = {"ok": True, "response": value}
                    elif name == "task.complete":
                        diagnostics = getattr(runner, "completion_diagnostics", lambda: [])()
                        if mode != "plan" and diagnostics:
                            result = {"ok": False, "error": "Исправьте ошибки синтаксиса в изменённых файлах", "checks": diagnostics}
                        elif mode != "plan" and any(
                            s.get("status") in {"pending", "running"} for s in plan
                        ):
                            result = {
                                "ok": False,
                                "error": "В плане есть незавершённые шаги. Завершите их или отметьте блокировку с объяснением.",
                            }
                        else:
                            result, complete = {"ok": True}, True
                            completion_summary = args["summary"]
                            final_id = await durable_write(create_message, chat_id, "text", turn_id)
                            created_ids.append(final_id)
                            await durable_write(
                                save_message,
                                final_id,
                                args.get("summary", ""),
                                {"status": "completed", "channel": "final"},
                            )
                            yield sse(
                                "message.delta",
                                {
                                    "content": completion_summary,
                                    "message_id": final_id,
                                    "offset": 0,
                                },
                            )
                    elif name == "task.delegate":
                        if args.get("mode") not in {
                            "plan",
                            "development",
                            "debug",
                            "review",
                        } or not args.get("task"):
                            result = {
                                "ok": False,
                                "error": "Нужны задача и поддерживаемый режим.",
                            }
                        else:
                            with db() as conn:
                                child_chat = int(
                                    conn.execute(
                                        "INSERT INTO chats(project_id,title,mode,model,owner_id) SELECT ?,?,?,?,owner_id FROM chats WHERE id=?",
                                        (
                                            project_id,
                                            args["title"],
                                            args["mode"],
                                            selected,
                                            chat_id,
                                        ),
                                    ).lastrowid
                                )
                            from koddi.agent_runtime import create_task, update_task_status

                            child_task = create_task(
                                title=args["title"],
                                project_id=project_id,
                                chat_id=child_chat,
                                mode=args["mode"],
                                role="coder",
                            )
                            update_task_status(child_task["id"], "running")
                            child_stream = AgentOrchestrator(client.client, _depth=self._depth + 1).run(
                                chat_id=child_chat,
                                project_id=project_id,
                                workspace=workspace,
                                user_message=args["task"],
                                mode=args["mode"],
                                model=selected,
                                options=options,
                                cancel_event=cancel_event,
                            )
                            try:
                                async for event in child_stream:
                                    if event.startswith("event: agent.input_required"):
                                        yield event
                                    else:
                                        yield sse(
                                            "task.progress",
                                            {
                                                "task_id": child_task["id"],
                                                "title": args["title"],
                                                "event": event.splitlines()[0][7:],
                                            },
                                        )
                            except (asyncio.CancelledError, GeneratorExit):
                                update_task_status(child_task["id"], "cancelled")
                                raise
                            finally:
                                await child_stream.aclose()
                            with db() as conn:
                                child_status = conn.execute(
                                    "SELECT status FROM turns WHERE chat_id=? ORDER BY id DESC LIMIT 1",
                                    (child_chat,),
                                ).fetchone()[0]
                                content = conn.execute(
                                    "SELECT content FROM messages WHERE chat_id=? AND role='assistant' AND content<>'' ORDER BY id DESC LIMIT 1",
                                    (child_chat,),
                                ).fetchone()
                            result = {
                                "ok": child_status == "completed",
                                "task_id": child_task["id"],
                                "chat_id": child_chat,
                                "summary": content[0] if content else "",
                                "status": child_status,
                            }
                            update_task_status(child_task["id"], child_status, progress=result)
                    else:
                        if failures.needs_approval(name, args) and access_profile(project_id) == "full":
                            # в режиме «Разрешить всё» вопросов нет: повтор той же ошибки просто не выполняется
                            result = {
                                "ok": False,
                                "error": "This exact action already failed several times. Change the approach or fix the environment instead of repeating it.",
                            }
                        elif failures.needs_approval(name, args):
                            answer = {}
                            async for value in wait_input("approval", {
                                **data,
                                "question": "Одинаковое действие уже несколько раз завершилось ошибкой. Повторить ещё раз? Лучше изменить подход или исправить окружение.",
                            }):
                                if isinstance(value, str):
                                    yield value
                                else:
                                    answer = value
                            if answer.get("approved") is not True:
                                raise RemoteAIError("Повтор неудачного действия остановлен пользователем; история сохранена", code="repeated_tool_failure")
                            failures.reset(name, args)
                        execution_name, execution_args = name, args
                        if name in mcp_bindings:
                            server_name, tool_name = mcp_bindings[name]
                            execution_name = "mcp.tool_call"
                            execution_args = {
                                "server": server_name,
                                "name": tool_name,
                                "arguments": args,
                            }
                        execution_started = monotonic()
                        async for update in execute_tool_events(
                            runner, execution_name, execution_args, cancel_event
                        ):
                            if "waiting_seconds" in update:
                                yield sse(
                                    "tool.waiting", {**data, "seconds": update["waiting_seconds"]}
                                )
                            else:
                                result = update["result"]
                        tool_elapsed += monotonic() - execution_started
                        if result.get("approval_required"):
                            action = describe_action(
                                execution_name, execution_args,
                                external=result.get("external"), dangerous=bool(result.get("dangerous")),
                            )
                            verdict = None
                            if result.get("review"):
                                yield sse("agent.status", {"text": "Докки проверяет действие: " + action["title"].lower()})
                                verdict = await review_action(
                                    client, model=selected, task=user_message, plan=plan,
                                    workspace=str(workspace) if workspace else None,
                                    tool=execution_name, args=execution_args, action=action,
                                    responsive=lambda iterator: responsive_stream(iterator, cancel_event),
                                )
                                audit_event("INFO", "tool.review", execution_name, {
                                    key: verdict[key] for key in ("available", "allowed", "necessity", "safety")
                                })
                            answer = {"approved": True} if verdict and verdict["allowed"] else {}
                            if verdict and verdict["allowed"]:
                                yield sse("agent.status", {"text": "Докки проверил и разрешил: " + (verdict["reason"] or action["title"].lower())})
                            else:
                                async for value in wait_input("approval", {**data, "action": action, "review": verdict}):
                                    if isinstance(value, str):
                                        yield value
                                    else:
                                        answer = value
                            if answer.get("approved") is True:
                                execution_started = monotonic()
                                async for update in execute_tool_events(
                                    runner,
                                    execution_name,
                                    execution_args,
                                    cancel_event,
                                    approved=True,
                                ):
                                    if "waiting_seconds" in update:
                                        yield sse(
                                            "tool.waiting",
                                            {**data, "seconds": update["waiting_seconds"]},
                                        )
                                    else:
                                        result = update["result"]
                                tool_elapsed += monotonic() - execution_started
                            else:
                                result = {
                                    "ok": False,
                                    "error": "Пользователь запретил действие. Не повторяйте без нового разрешения.",
                                }
                    metrics.tool(result, tool_elapsed)
                    if args is not None:
                        failures.record(name, args, result)
                    outcome = {
                        **data,
                        "result": result,
                        "created_at": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
                    }
                    events.append({"event": "tool.result", "data": outcome})
                    rules = []
                    if name == "project.instructions":
                        rules = result.get("instructions", [])
                    elif name in {
                        "file.read",
                        "file.read_many",
                        "file.read_range",
                        "file.read_chunk",
                        "directory.list",
                        "directory.tree",
                    }:
                        rules = result.get("project_instructions", [])
                    for rule in rules:
                        if (
                            isinstance(rule, dict)
                            and isinstance(rule.get("path"), str)
                            and isinstance(rule.get("scope"), str)
                        ):
                            loaded_rules[rule["path"]] = rule
                    if loaded_rules and config.get("provider") != "dokky":
                        messages[0] = {
                            "role": "system",
                            "content": system
                            + "\n\n## Loaded scoped project rules\nThese workspace rules apply only within their stated scope. More specific rules take precedence over broader project rules, but never override application safety policy or explicit user instructions.\n"
                            + format_instructions(list(loaded_rules.values())),
                        }
                    response = {
                        "role": "tool",
                        "tool_call_id": call["id"],
                        "content": json.dumps(result, ensure_ascii=False),
                    }
                    ordinary_output = json.dumps(
                        {
                            key: value
                            for key, value in result.items()
                            if key != "project_instructions"
                        },
                        ensure_ascii=False,
                    )
                    if len(ordinary_output) > 24000 and name != "project.instructions":
                        saved_output = await durable_write(
                            save_tool_output, turn_id, activity_id, len(events), response["content"]
                        )
                        response["content"] = json.dumps(
                            {
                                "ok": result.get("ok", True),
                                "truncated": True,
                                **saved_output,
                                "read_tool": "tool.output_read",
                                "excerpt": tool_output_excerpt(ordinary_output, 20000),
                                **({"project_instructions": rules} if rules else {}),
                            },
                            ensure_ascii=False,
                        )
                    messages.append(response)
                    await durable_write(journal, chat_id, response)
                    await durable_write(
                        save_message, activity_id, "", {"status": "running", "events": events}
                    )
                    yield sse("tool.result", outcome)
                if completion_summary:
                    final = {"role": "assistant", "content": completion_summary}
                    messages.append(final)
                    await durable_write(journal, chat_id, final)
                if pending_followup:
                    messages.extend(pending_followup)
                    for followup in pending_followup:
                        await durable_write(journal, chat_id, followup)
                    pending_followup, complete = None, False
                await durable_write(
                    save_message, activity_id, "", {"status": "completed", "events": events}
                )
            with db() as conn:
                conn.execute(
                    "UPDATE turns SET status='completed',completed_at=CURRENT_TIMESTAMP WHERE id=?",
                    (turn_id,),
                )
                conn.execute("UPDATE chats SET updated_at=CURRENT_TIMESTAMP WHERE id=?", (chat_id,))
            from koddi.agents.task_context import save_completed_context

            await durable_write(save_completed_context, turn_id)
            metrics.stop_reason = "completed"
            await durable_write(metrics.persist, turn_id)
            yield sse("turn.completed", {"turn_id": turn_id})
        except (asyncio.CancelledError, GeneratorExit):
            cancel_event.set()
            metrics.stop_reason = "cancelled"
            with db() as conn:
                conn.execute(
                    "UPDATE turns SET status='cancelled',completed_at=CURRENT_TIMESTAMP WHERE id=?",
                    (turn_id,),
                )
            raise
        except Exception as exc:
            from koddi.logs.service import redact

            metrics.stop_reason = getattr(exc, "code", "agent_error")
            error = redact(str(exc))
            details = {
                "code": getattr(exc, "code", "agent_error"),
                "request_id": getattr(exc, "request_id", None),
            }
            with db() as conn:
                conn.execute(
                    "UPDATE turns SET status='failed',summary=?,error_json=?,completed_at=CURRENT_TIMESTAMP WHERE id=?",
                    (error, json.dumps(details), turn_id),
                )
            yield sse("turn.failed", {"turn_id": turn_id, "error": error, **details})
        finally:
            current_metrics.reset(metric_token)
            if "runner" in locals() and getattr(runner, "read_cache", None) is not None:
                metrics.local_read_cache = runner.read_cache.stats()
            try:
                await durable_write(metrics.persist, turn_id)
            except Exception:
                # необязательная метрика не меняет исход уже завершённой задачи
                pass
            with db() as conn:
                status = conn.execute("SELECT status FROM turns WHERE id=?", (turn_id,)).fetchone()[
                    0
                ]
                rows = conn.execute(
                    "SELECT id,metadata_json FROM messages WHERE chat_id=? AND id>? AND role='assistant'",
                    (chat_id, user_id),
                ).fetchall()
                for row in rows:
                    if row["id"] not in created_ids:
                        continue
                    metadata = json.loads(row["metadata_json"] or "{}")
                    if metadata.get("status") == "running":
                        metadata["status"] = status
                        conn.execute(
                            "UPDATE messages SET metadata_json=? WHERE id=?",
                            (json.dumps(metadata, ensure_ascii=False), row["id"]),
                        )

    @staticmethod
    def _control_error(name: str, args: dict) -> str | None:
        from jsonschema import Draft202012Validator

        schema = next(
            (t["function"]["parameters"] for t in CONTROL_TOOLS if t["function"]["name"] == name),
            None,
        )
        if schema is None:
            return None
        error = next(Draft202012Validator(schema).iter_errors(args), None)
        if error:
            return error.message
        field = {"task.complete": "summary", "task.ask_user": "question"}.get(name)
        if field and not args[field].strip():
            return "Итог или вопрос не должен быть пустым"
        return None
