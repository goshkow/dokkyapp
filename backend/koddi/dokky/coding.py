from __future__ import annotations

import json
import copy
import hashlib
import re
import threading
import uuid
from contextlib import contextmanager

from koddi.dokky.client import DokkyError
from koddi.storage.db import db, get_setting

MODE_WARNING = (
    "Смена режима может увеличить стоимость следующего запроса, поскольку ИИ может потребоваться "
    "повторно обработать историю диалога. Сама история сохранится."
)
MODES = {"light", "pro", "max"}
_active: set[tuple[str, int | str]] = set()
_active_lock = threading.Lock()


@contextmanager
def session_operation(chat_id: int | str):
    identity = owner()
    key = (identity, chat_id)
    with _active_lock:
        if key in _active:
            raise DokkyError("В этом диалоге уже выполняется запрос", 409, "coding_busy")
        _active.add(key)
    try:
        yield identity
    finally:
        with _active_lock:
            _active.discard(key)


def check_owner(identity: str) -> None:
    if owner() != identity:
        raise DokkyError("Вход изменился. Повторите действие", 409, "session_changed")


def account_client():
    from koddi.dokky.client import dokky

    return dokky


def owner() -> str:
    user = get_setting("dokky_user", {})
    value = user.get("id") or user.get("user_id")
    if not isinstance(value, (str, int)) or isinstance(value, bool) or not str(value):
        raise DokkyError("Войдите с помощью Докки", 401, "invalid_token")
    return str(value)


def load(chat_id: int) -> dict | None:
    with db() as conn:
        row = conn.execute(
            "SELECT * FROM coding_sessions WHERE chat_id=? AND user_id=?", (chat_id, owner())
        ).fetchone()
    if not row:
        return None
    try:
        session = json.loads(row["session_json"])
        validate_session(session)
    except (ValueError, TypeError, DokkyError):
        return None
    try:
        tools = json.loads(row["tools_json"])
        if not isinstance(tools, list):
            raise ValueError
    except (ValueError, TypeError):
        tools = []
    try:
        messages = json.loads(row["messages_json"])
        history = json.loads(row["history_json"] or "null")
        if not isinstance(messages, list) or not all(
            isinstance(item, dict)
            and item.get("role") in {"system", "developer", "user", "assistant", "tool"}
            for item in messages
        ):
            raise ValueError
        if not isinstance(tools, list) or (history is not None and not isinstance(history, dict)):
            raise ValueError
        return {"session": session, "messages": messages, "tools": tools, "history": history}
    except (ValueError, TypeError):
        return {"session": session, "messages": [], "tools": tools, "history": None, "_recover": True}


def save(chat_id: int, value: dict, *, finished_key: str | None = None, expected_owner: str | None = None) -> None:
    identity = expected_owner or owner()
    with db() as conn:
        if expected_owner is not None:
            conn.execute('BEGIN IMMEDIATE')
            account = conn.execute("SELECT value_json FROM settings WHERE key='dokky_user'").fetchone()
            account = json.loads(account[0]) if account else {}
            current = account.get('id') or account.get('user_id')
            if str(current) != identity:
                raise DokkyError('Вход изменился. Повторите действие', 409, 'session_changed')
        conn.execute(
            "INSERT INTO coding_sessions(chat_id,user_id,session_json,messages_json,tools_json,history_json) "
            "VALUES (?,?,?,?,?,?) ON CONFLICT(chat_id) DO UPDATE SET user_id=excluded.user_id, "
            "session_json=excluded.session_json,messages_json=excluded.messages_json, "
            "tools_json=excluded.tools_json,history_json=excluded.history_json,updated_at=CURRENT_TIMESTAMP",
            (
                chat_id,
                identity,
                json.dumps(value["session"], ensure_ascii=False),
                json.dumps(value["messages"], ensure_ascii=False),
                json.dumps(value["tools"], ensure_ascii=False),
                json.dumps(value["history"]),
            ),
        )
        if finished_key:
            conn.execute(
                "DELETE FROM coding_pending WHERE chat_id=? AND request_key=? AND user_id=?",
                (chat_id, finished_key, identity),
            )


def _schema_has_reference(value: object) -> bool:
    if isinstance(value, dict):
        return any(key in value for key in ("$ref", "$dynamicRef", "$recursiveRef")) or any(
            _schema_has_reference(item) for item in value.values()
        )
    return isinstance(value, list) and any(_schema_has_reference(item) for item in value)


def _accepts_pinned_schema(pinned: object, current: object) -> bool:
    if pinned == current:
        return True
    if not isinstance(pinned, dict) or not isinstance(current, dict):
        return False
    # ссылки могут связывать свойства с другими частями схемы; их изменения требуют миграции
    if _schema_has_reference(pinned) or _schema_has_reference(current):
        return False
    handled = {"description", "title", "$comment", "examples", "properties", "required", "enum", "items"}
    if {k: v for k, v in pinned.items() if k not in handled} != {
        k: v for k, v in current.items() if k not in handled
    }:
        return False
    old_required, new_required = pinned.get("required", []), current.get("required", [])
    if not isinstance(old_required, list) or not isinstance(new_required, list):
        return False
    if any(key not in old_required for key in new_required):
        return False
    old_properties, new_properties = pinned.get("properties", {}), current.get("properties", {})
    if not isinstance(old_properties, dict) or not isinstance(new_properties, dict):
        return False
    if old_properties.keys() - new_properties.keys():
        return False
    # новое свойство с ограничениями совместимо только если раньше оно было запрещено
    if new_properties.keys() - old_properties.keys() and pinned.get("additionalProperties") is not False:
        return False
    if any(not _accepts_pinned_schema(schema, new_properties[name]) for name, schema in old_properties.items()):
        return False
    if "enum" in current:
        if not isinstance(current["enum"], list) or not isinstance(pinned.get("enum"), list):
            return False
        encoded = {json.dumps(value, sort_keys=True) for value in current["enum"]}
        if any(json.dumps(value, sort_keys=True) not in encoded for value in pinned["enum"]):
            return False
    if "items" in pinned or "items" in current:
        if "items" not in pinned or "items" not in current:
            return False
        if not _accepts_pinned_schema(pinned["items"], current["items"]):
            return False
    return True


def compatible_tools(pinned: list[dict], available: list[dict]) -> list[dict]:
    if not pinned:
        return available
    current={item['function']['name'].replace('.','__'):item for item in available}
    result=[]
    for item in pinned:
        function=item.get('function',{})
        candidate=current.get(function.get('name', '').replace('.', '__'))
        if candidate is None or not _accepts_pinned_schema(function.get('parameters'), candidate['function']['parameters']):
            raise DokkyError('Инструменты этого диалога больше не совместимы. История сохранена; создайте новый чат.',409,'tools_changed')
        # серверная сессия закрепляет порядок и точные схемы, включая описания
        result.append({'type':'function','function':dict(function,name=candidate['function']['name'])})
    return result


def pending(chat_id: int) -> dict | None:
    with db() as conn:
        row = conn.execute(
            "SELECT * FROM coding_pending WHERE chat_id=? AND user_id=?", (chat_id, owner())
        ).fetchone()
    return (
        {
            "key": row["request_key"],
            "body": json.loads(row["body_json"]),
            "messages": json.loads(row["messages_json"]),
        }
        if row
        else None
    )


def save_pending(chat_id: int, key: str, body: dict, messages: list) -> None:
    with db() as conn:
        conn.execute(
            "INSERT INTO coding_pending(chat_id,user_id,request_key,body_json,messages_json) VALUES (?,?,?,?,?) "
            "ON CONFLICT(chat_id) DO UPDATE SET request_key=excluded.request_key,body_json=excluded.body_json,messages_json=excluded.messages_json WHERE coding_pending.user_id=excluded.user_id",
            (
                chat_id,
                owner(),
                key,
                json.dumps(body, ensure_ascii=False),
                json.dumps(messages, ensure_ascii=False),
            ),
        )


def discard_pending(chat_id: int, key: str) -> None:
    with db() as conn:
        conn.execute(
            "DELETE FROM coding_pending WHERE chat_id=? AND user_id=? AND request_key=?",
            (chat_id, owner(), key),
        )


def unconfirmed_tail(canonical: list, journal: list) -> list:
    def normalized(message):
        result = json.loads(json.dumps(message))
        for call in result.get("tool_calls", []):
            call["function"]["name"] = call["function"]["name"].replace(".", "__")
        return result

    for known in reversed(canonical):
        for index in range(len(journal) - 1, -1, -1):
            if normalized(known) == normalized(journal[index]):
                return journal[index + 1 :]
    return journal


def journal_since_recovery(chat_id: int, value: dict, fallback: list) -> list:
    cutoff = value["session"].get("journal_rebase_after")
    if type(cutoff) is not int or cutoff < 0:
        return fallback
    from koddi.agents.history import replay_history

    with db() as conn:
        rows = conn.execute(
            "SELECT payload_json FROM agent_history WHERE chat_id=? AND id>? ORDER BY id",
            (chat_id, cutoff),
        ).fetchall()
    return replay_history([json.loads(row[0]) for row in rows])


def isolate_recovered_journal(chat_id: int, value: dict) -> None:
    with db() as conn:
        cutoff = conn.execute("SELECT COALESCE(MAX(id),0) FROM agent_history WHERE chat_id=?", (chat_id,)).fetchone()[0]
    value["session"]["journal_rebase_after"] = cutoff
    save(chat_id, value)


async def ensure(chat_id: int, client=None) -> dict:
    client = client or account_client()
    value = load(chat_id)
    if value:
        if value.pop("_recover", False):
            save(chat_id, value)
            return await recover(chat_id, client)
        return value
    with db() as conn:
        existing_key = conn.execute(
            "SELECT 1 FROM coding_session_keys WHERE chat_id=?", (chat_id,)
        ).fetchone()
        chat = conn.execute("SELECT title FROM chats WHERE id=?", (chat_id,)).fetchone()
    if not chat:
        raise DokkyError("Чат не найден", 404, "not_found")
    from koddi.policy.preferences import reasoning_mode

    mode = reasoning_mode()
    with db() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO coding_session_keys(chat_id,user_id,request_key,body_json) VALUES (?,?,?,?)",
            (
                chat_id,
                owner(),
                str(uuid.uuid4()),
                json.dumps({"title": chat[0][:200], "reasoning_mode": mode}),
            ),
        )
        creation = conn.execute(
            "SELECT request_key,body_json FROM coding_session_keys WHERE chat_id=? AND user_id=?",
            (chat_id, owner()),
        ).fetchone()
    if not creation:
        raise DokkyError("Этот диалог принадлежит другому аккаунту", 403, "forbidden")
    session = (
        await client.request(
            "POST", "/coding/sessions", key=creation[0], json=json.loads(creation[1])
        )
    ).json()
    validate_session(session)
    if existing_key:
        session = (await client.request("GET", "/coding/sessions/" + session["id"])).json()
        validate_session(session)
    value = {"session": session, "messages": [], "tools": [], "history": None}
    save(chat_id, value)
    if existing_key and session.get("history", {}).get("length", 0):
        return await recover(chat_id, client)
    return value


def turn_mode(chat_id: int) -> str:
    """Режим, в котором пойдёт следующее сообщение: отложенная смена применяется именно с ним."""
    value = load(chat_id)
    session = value["session"] if value else {}
    from koddi.policy.preferences import reasoning_mode

    mode = session.get("pending_reasoning_mode") or session.get("reasoning_mode") or reasoning_mode()
    return mode if mode in MODES else "pro"


def validate_session(session: dict) -> None:
    if (
        not isinstance(session, dict)
        or not re.fullmatch(r"cs_[a-f0-9]{24}", str(session.get("id", "")))
        or not isinstance(session.get("reasoning_mode"), str)
        or session["reasoning_mode"] not in MODES
        or type(session.get("version")) is not int
    ):
        raise DokkyError("Докки вернул некорректную сессию разработки")


def replace_remote_session(value: dict, updated: dict) -> None:
    validate_session(updated)
    local_fields = {key: value["session"][key] for key in ("client_cache", "journal_rebase_after", "context_transition", "context_policy", "previous_session_id", "pending_reasoning_mode") if key in value["session"]}
    value["session"] = {**updated, **local_fields}


def public_session(session: dict) -> dict:
    return {key: value for key, value in session.items() if key != 'context_transition'} | {
        'compaction_pending': bool(session.get('context_transition')),
        # смена режима у текущей модели сервера не теряет кэш: интерфейс не спрашивает подтверждения
        'cache_preserved': cache_preserved(session),
    }


async def refresh(chat_id: int, client=None) -> dict:
    client = client or account_client()
    value = await ensure(chat_id, client)
    updated = (
        await client.request("GET", "/coding/sessions/" + value["session"]["id"])
    ).json()
    replace_remote_session(value, updated)
    save(chat_id, value)
    return value


async def recover(chat_id: int, client=None) -> dict:
    client = client or account_client()
    value = await refresh(chat_id, client)
    identity = owner()
    messages, after = [], -1
    expected = value["session"].get("history")
    total_chars, chain_hash = 0, ""
    while True:
        page = (
            await client.request(
                "GET",
                f"/coding/sessions/{value['session']['id']}/history",
                params={"after": after, "limit": 200},
            )
        ).json()
        check_owner(identity)
        if page.get("history", expected) != expected:
            raise DokkyError("История изменилась во время загрузки. Повторите действие", 409, "history_changed")
        items = page.get("items", [])
        if not isinstance(items, list) or len(items) > 200:
            raise DokkyError("Докки вернул некорректную страницу истории")
        for item in items:
            message = item.get("message")
            if not isinstance(message, dict) or message.get("role") not in {"system", "developer", "user", "assistant", "tool"}:
                raise DokkyError("Докки вернул некорректное сообщение истории")
            if "seq" in item and item["seq"] != len(messages):
                raise DokkyError("Нарушен порядок истории Докки")
            raw = json.dumps(message, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
            content_hash = hashlib.sha256(raw.encode()).hexdigest()
            chain_hash = hashlib.sha256((chain_hash + content_hash).encode()).hexdigest()
            if ("content_hash" in item and item["content_hash"] != content_hash) or ("chain_hash" in item and item["chain_hash"] != chain_hash):
                raise DokkyError("Не совпадает контрольная сумма истории Докки")
            total_chars += len(raw)
            if len(messages) >= 4001 or total_chars > 3_000_000:
                raise DokkyError("История Докки превышает поддерживаемый размер")
            messages.append(message)
        next_after = page.get("next_after")
        if next_after is None:
            break
        if type(next_after) is not int or next_after <= after or not items:
            raise DokkyError("Докки вернул некорректную страницу истории")
        after = next_after
    if expected and len(messages) != expected.get("length"):
        raise DokkyError("История Докки загружена не полностью")
    value["messages"] = messages
    value["history"] = value["session"].get("history")
    save(chat_id, value)
    return value


def cache_preserved(session: dict) -> bool:
    """Последний ответ сервера сообщил, что смена режима у текущей модели не теряет кэш (Haiku, Luna Responses)."""
    context = ((session.get("last_usage") or {}).get("dokky") or {}).get("context") or {}
    return isinstance(context, dict) and context.get("reasoning_switch_cache") == "preserved"


async def switch_mode(chat_id: int, mode: str, acknowledge: bool, client=None) -> dict:
    """Выбор режима. В диалоге с историей режим только запоминается и применяется со следующим сообщением.

    Возврат к режиму, который стоит сейчас, снимает отложенную смену сразу и без вопросов. Пока сообщение не
    отправлено, ни расчёт плана, ни сжатие, ни запросы к серверу не выполняются.
    """
    if not isinstance(mode, str) or mode not in MODES:
        raise DokkyError("Неизвестный режим разработки", 422, "validation_error")
    with session_operation(chat_id):
        value = await ensure(chat_id, client)
        with session_operation(value["session"]["id"]):
            session = value["session"]
            if mode == session.get("reasoning_mode", "pro"):
                if session.pop("pending_reasoning_mode", None) is not None:
                    save(chat_id, value)
                return session
            with db() as conn:
                has_messages = conn.execute(
                    "SELECT 1 FROM messages WHERE chat_id=? LIMIT 1", (chat_id,)
                ).fetchone()
            if not (has_messages or session.get("history", {}).get("length", 0)):
                session.pop("pending_reasoning_mode", None)
                return await _switch_mode(chat_id, mode, acknowledge, client)
            if not acknowledge and not cache_preserved(session):
                raise DokkyError(MODE_WARNING, 409, "confirmation_required")
            if mode != session.get("pending_reasoning_mode"):
                session["pending_reasoning_mode"] = mode
                save(chat_id, value)
            return session


async def apply_pending_mode(chat_id: int, *, client=None, config=None, runtime_context=None):
    """Выполняет отложенную смену режима перед первым запросом нового сообщения.

    Порядок по руководству: план смены с сервера, затем `switch`, чистка выводов или сжатие старым
    режимом. Генератор отдаёт события интерфейса (статус и сжатие контекста).
    """
    client = client or account_client()
    value = load(chat_id)
    if not value or not value["session"].get("pending_reasoning_mode"):
        return
    with session_operation(chat_id):
        value = await ensure(chat_id, client)
        with session_operation(value["session"]["id"]):
            for attempt in range(2):
                session = value["session"]
                mode = session.get("pending_reasoning_mode")
                if mode is None:
                    return
                if mode not in MODES or mode == session.get("reasoning_mode"):
                    session.pop("pending_reasoning_mode", None)
                    save(chat_id, value)
                    return
                if pending(chat_id):
                    raise DokkyError("Сначала восстановите незавершённый запрос", 409, "coding_busy")
                yield {"event": "agent.status", "text": "Меняю режим…"}
                if value["messages"]:
                    from koddi.dokky.context import prune_outputs, transition

                    plan = (await client.request(
                        "GET", f"/coding/sessions/{session['id']}/switch-plan", params={"mode": mode}
                    )).json()
                    action = plan.get("recommended")
                    if plan.get("to", mode) != mode or action not in {"switch", "prune_then_switch", "compress_then_switch"}:
                        raise DokkyError("Докки вернул некорректный план смены режима")
                    if action != "switch" and (
                        action != "prune_then_switch" or prune_outputs(value["messages"]) != value["messages"]
                    ):
                        async for event in transition(
                            chat_id,
                            target_mode=mode,
                            action=action,
                            client=client,
                            config=config,
                            runtime_context=runtime_context,
                            locked=True,
                        ):
                            yield event
                        return
                try:
                    updated = (
                        await client.request(
                            "PATCH",
                            "/coding/sessions/" + session["id"],
                            json={
                                "expected_version": session["version"],
                                "reasoning_mode": mode,
                                "acknowledge_cache_cost": True,
                            },
                        )
                    ).json()
                    replace_remote_session(value, updated)
                    value["session"].pop("pending_reasoning_mode", None)
                    save(chat_id, value)
                    return
                except DokkyError as exc:
                    if exc.code != "conflict" or attempt:
                        raise
                    value = await refresh(chat_id, client)


async def _switch_mode(chat_id: int, mode: str, acknowledge: bool, client=None) -> dict:
    client = client or account_client()
    value = await ensure(chat_id, client)
    for attempt in range(2):
        session = value["session"]
        if mode == session.get("reasoning_mode", "pro"):
            return session
        with db() as conn:
            has_messages = conn.execute(
                "SELECT 1 FROM messages WHERE chat_id=? LIMIT 1", (chat_id,)
            ).fetchone()
        if (has_messages or session.get("history", {}).get("length", 0)) and not acknowledge and not cache_preserved(session):
            raise DokkyError(MODE_WARNING, 409, "confirmation_required")
        if pending(chat_id):
            raise DokkyError("Сначала восстановите незавершённый запрос", 409, "coding_busy")
        if value["messages"] and acknowledge:
            from koddi.dokky.context import transition

            plan = (await client.request(
                "GET", f"/coding/sessions/{session['id']}/switch-plan", params={"mode": mode}
            )).json()
            action = plan.get("recommended")
            if plan.get("to", mode) != mode or action not in {"switch", "prune_then_switch", "compress_then_switch"}:
                raise DokkyError("Докки вернул некорректный план смены режима")
            if action != "switch":
                from koddi.dokky.context import prune_outputs

                if action != "prune_then_switch" or prune_outputs(value["messages"]) != value["messages"]:
                    async for _ in transition(chat_id, target_mode=mode, action=action, client=client, locked=True):
                        pass
                    updated = load(chat_id)["session"]
                    updated["switch_plan"] = plan
                    return updated
        try:
            updated = (
                await client.request(
                    "PATCH",
                    "/coding/sessions/" + session["id"],
                    json={
                        "expected_version": session["version"],
                        "reasoning_mode": mode,
                        "acknowledge_cache_cost": acknowledge or cache_preserved(session),
                    },
                )
            ).json()
            replace_remote_session(value, updated)
            save(chat_id, value)
            return value["session"]
        except DokkyError as exc:
            if exc.code != "conflict" or attempt:
                raise
            value = await refresh(chat_id, client)
    raise AssertionError


def same_message(left: dict, right: dict) -> bool:
    if left == right:
        return True
    left, right = copy.deepcopy(left), copy.deepcopy(right)
    for message in (left, right):
        for call in message.get("tool_calls", []):
            arguments = call.get("function", {}).get("arguments")
            if isinstance(arguments, str):
                try:
                    call["function"]["arguments"] = json.loads(arguments)
                except ValueError:
                    return False
    return left == right


def canonical_messages(value: dict, messages: list, *, checkpoint: bool = False) -> list:
    previous = value["messages"]
    if checkpoint and previous:
        def instructions(items):
            result = []
            for item in items:
                if item.get("role") not in {"system", "developer"}:
                    break
                result.append(item)
            return result

        if instructions(previous) != instructions(messages):
            raise DokkyError("Начальные инструкции сессии нельзя менять при сжатии", 409, "system_prompt_changed")
    if not previous or checkpoint:
        return copy.deepcopy(messages)
    if len(messages) >= len(previous) and all(same_message(a, b) for a, b in zip(previous, messages)):
        # канонические аргументы сохраняются строкой, даже если локальный журнал пересобрал JSON
        return copy.deepcopy(previous + messages[len(previous):])
    from koddi.agents.history import without_empty_assistants

    cleaned = without_empty_assistants(previous)
    if cleaned != previous and messages[:len(cleaned)] == cleaned:
        return copy.deepcopy(messages)
    raise DokkyError("Отправленная история изменена. Сначала восстановите серверную историю", 409, "history_changed")


def rebase_pending(previous: list, proposed: list, recovered: list) -> list:
    if proposed[:len(previous)] != previous or recovered[:len(previous)] != previous:
        raise DokkyError("Истории разошлись. Контекст восстановлен с сервера; локальная лента сохранена как журнал. Неподтверждённая задача не повторена: отправьте её новым сообщением", 409, "history_changed")
    tail, remote_tail = proposed[len(previous):], recovered[len(previous):]
    common = 0
    while common < min(len(tail), len(remote_tail)) and tail[common] == remote_tail[common]:
        common += 1
    if remote_tail[common:] and tail[common:]:
        raise DokkyError("Сервер уже продолжил диалог. Локальная лента сохранена как журнал; неподтверждённую задачу отправьте новым сообщением", 409, "history_changed")
    result = copy.deepcopy(recovered + tail[common:])
    if result == recovered:
        raise DokkyError("Запрос уже присутствует в серверной истории. Повтор не отправлен", 409, "request_already_saved")
    return result


def prepare(value: dict, messages: list, tools: list, *, checkpoint: bool = False) -> dict:
    if value["session"].get("context_transition"):
        raise DokkyError("Сначала завершите сжатие контекста", 409, "compaction_pending")
    messages = canonical_messages(value, messages, checkpoint=checkpoint)
    previous = value["messages"]
    history = value.get("history")
    body = {
        "model": "dokky-code",
        "session_id": value["session"]["id"],
        "store": False,
        "reasoning_mode": value["session"].get("reasoning_mode", "pro"),
        "messages": messages,
    }
    # дельта допустима только при точном совпадении канонического префикса
    if history and history.get("length") == len(previous) and messages[: len(previous)] == previous:
        tail = messages[len(previous) :]
        if tail:
            body["history"] = {"base_length": history["length"], "base_hash": history["hash"]}
            body["messages"] = tail
    if tools:
        body.update(tools=tools, tool_choice="auto")
    return body


async def auxiliary_session(client=None) -> dict:
    client = client or account_client()
    session = (
        await client.request(
            "POST",
            "/coding/sessions",
            key=str(uuid.uuid4()),
            json={"title": "Название чата", "reasoning_mode": "light"},
        )
    ).json()
    validate_session(session)
    return session


def clear_local_account_data() -> None:
    """Выход из аккаунта убирает только то, что принадлежит самому аккаунту: сессии и ключи кодинга, разрешения
    проектов, ожидающие подтверждения, кэши и профиль браузера агента. Локальные чаты, сообщения, журнал, проекты и вложения
    остаются на компьютере и открываются после повторного входа владельца."""
    from koddi.agents.context_cache import prompt_context_cache

    prompt_context_cache.clear()
    with db() as conn:
        conn.execute("DELETE FROM coding_session_keys")
        conn.execute("DELETE FROM project_grants")
        conn.execute("DELETE FROM coding_pending")
        conn.execute("DELETE FROM coding_sessions")
        conn.execute("DELETE FROM agent_requests")
        conn.execute("DELETE FROM browser_sessions")
    from koddi.config import data_dir
    import shutil

    profile = data_dir().resolve()
    for name in ("browser",):
        path = profile / name
        if (
            path.is_dir()
            and not path.is_symlink()
            and not (path.lstat().st_file_attributes & 0x400 if hasattr(path.lstat(), 'st_file_attributes') else False)
            and path.resolve().parent == profile
        ):
            shutil.rmtree(path)


# проекты и их чаты живут на компьютере, а не в аккаунте: после входа любым аккаунтом они остаются на месте
LOCAL_TABLES = ("chats", "attachments", "queued_requests", "change_sets", "file_operations")


def adopt_local_data(user_id: str) -> None:
    """Переводит локальные чаты, вложения, очередь, память проектов и журнал изменений на вошедший аккаунт.

    Серверные сессии разработки принадлежат аккаунту, который их создал: чужие удаляются, и следующий запрос
    заводит новую сессию текущего аккаунта, а контекст собирается из локальной истории чата.
    """
    if not user_id:
        return
    with db() as conn:
        for table in LOCAL_TABLES:
            conn.execute(f"UPDATE {table} SET owner_id=? WHERE owner_id<>?", (user_id, user_id))
        # память проекта - вместе с проектом, личная память без проекта остаётся у своего аккаунта
        conn.execute(
            "UPDATE memory_summaries SET owner_id=? WHERE owner_id<>? AND (project_id IS NOT NULL OR owner_id='')",
            (user_id, user_id),
        )
        # заметки карты описывают проект и переходят вместе с ним; совпавший заголовок остаётся у вошедшего аккаунта
        conn.execute("UPDATE OR IGNORE project_map_notes SET owner_id=? WHERE owner_id<>?", (user_id, user_id))
        for table in ("coding_sessions", "coding_session_keys", "coding_pending"):
            conn.execute(f"DELETE FROM {table} WHERE user_id<>?", (user_id,))
