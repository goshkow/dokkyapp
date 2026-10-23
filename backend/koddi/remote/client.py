from __future__ import annotations

import asyncio
import json
import os
import re
import uuid
from copy import deepcopy
from typing import Any
from urllib.parse import urlparse

import httpx

from koddi.storage.db import get_setting, set_setting

CONTEXT_CEILING = 600_000


class RemoteAIError(RuntimeError):
    def __init__(self, message: str, *, code: str = "remote_error", request_id: str | None = None):
        super().__init__(message)
        self.code = code
        self.request_id = request_id


class ContextLimitError(RemoteAIError):
    def __init__(self, message: str, limit: int | None = None):
        super().__init__(message, code="context_length_exceeded")
        self.limit = limit


async def sse_payloads(response: httpx.Response):
    data: list[str] = []
    size = 0
    async for line in response.aiter_lines():
        if not line:
            if data:
                yield "\n".join(data)
            data, size = [], 0
        elif line.startswith("data:"):
            value = line[5:].removeprefix(" ")
            size += len(value)
            if size > 2_000_000:
                raise RemoteAIError("Слишком большой фрагмент ответа AI-сервера")
            data.append(value)
    if data:
        yield "\n".join(data)


def remote_config() -> dict[str, Any]:
    if os.getenv("KODDI_INTERNAL_TEST") != "1":
        from koddi.dokky.client import API

        models = [
            item for item in get_setting("dokky_models", []) if item.get("id") == "dokky-code"
        ]
        selected = get_setting("selected_model", "")
        if selected not in {item["id"] for item in models}:
            selected = next(iter(models), {}).get("id", "")
        context = next(
            (item.get("context_tokens", 32768) for item in models if item["id"] == selected), 32768
        )
        return {
            "provider": "dokky",
            "base_url": API,
            "model": selected,
            "models": models,
            "context_tokens": min(200_000, context)
            if type(context) is int and context >= 4096
            else 200_000,
            "context_ceiling_tokens": 200_000,
            "api_key_env": "",
        }
    saved = get_setting("remote_ai", {})
    return {
        "provider": os.getenv("KODDI_AI_PROVIDER", saved.get("provider", "openai-compatible")),
        "base_url": os.getenv("KODDI_AI_BASE_URL", saved.get("base_url", "")),
        "model": os.getenv("KODDI_AI_MODEL", saved.get("model", "")),
        "api_key_env": saved.get("api_key_env", "KODDI_AI_API_KEY"),
        "context_tokens": int(saved.get("context_tokens", 32768)),
        "models": saved.get("models", []),
    }


def save_remote_config(value: dict[str, Any]) -> dict[str, Any]:
    if os.getenv("KODDI_INTERNAL_TEST") != "1":
        raise ValueError("Докки подключается только к Dokky. Используйте вход через аккаунт")
    allowed = {"provider", "base_url", "model", "api_key_env", "context_tokens", "models"}
    if set(value) - allowed:
        raise ValueError("Неизвестные настройки AI-сервера")
    current = remote_config() | value
    if current["provider"] not in {"dokky-test", "openai-compatible"}:
        raise ValueError("Провайдер должен быть dokky-test или openai-compatible")
    if not isinstance(current["base_url"], str) or not isinstance(current["model"], str):
        raise ValueError("Адрес сервера и модель должны быть строками")
    if not isinstance(current["api_key_env"], str) or not re.fullmatch(
        r"[A-Za-z_][A-Za-z0-9_]*", current["api_key_env"]
    ):
        raise ValueError("Укажите корректное имя переменной окружения")
    if not isinstance(current["models"], list) or any(
        not isinstance(item, dict) or not isinstance(item.get("id"), str) or not item["id"].strip()
        for item in current["models"]
    ):
        raise ValueError("Список моделей должен содержать объекты с идентификатором id")
    url = urlparse(current["base_url"])
    if url.scheme not in {"http", "https"} or not url.hostname or url.username or url.password:
        raise ValueError("Укажите HTTP(S)-адрес сервера без пароля в адресе")
    if url.query or url.fragment:
        raise ValueError("Адрес сервера не должен содержать query или fragment")
    if current["provider"] == "dokky-test":
        if url.hostname not in {"localhost", "127.0.0.1", "::1"}:
            raise ValueError("Тестовый Dokky разрешён только на локальном сервере")
        if "model" not in value:
            current["model"] = "dokky-test-scripted"
    try:
        context_tokens = int(current["context_tokens"])
    except (TypeError, ValueError) as exc:
        raise ValueError("Размер контекста должен быть целым числом") from exc
    if isinstance(current["context_tokens"], bool) or not 4096 <= context_tokens <= 2_000_000:
        raise ValueError("Размер контекста должен быть от 4096 до 2000000 токенов")
    current["base_url"] = current["base_url"].rstrip("/")
    current["context_tokens"] = context_tokens
    set_setting("remote_ai", current)
    if current["model"]:
        set_setting("selected_model", current["model"])
    return current


class RemoteAIClient:
    """Транспорт в формате chat completions: без локального рантайма и управления процессами."""

    def __init__(self, config: dict[str, Any] | None = None, *, transport=None):
        self.config = config or remote_config()
        if transport is None and os.getenv("KODDI_INTERNAL_TEST") != "1":
            self.config = remote_config()
        self.transport = transport

    def _client(self) -> httpx.AsyncClient:
        base = self.config.get("base_url", "").rstrip("/")
        if not base:
            raise RemoteAIError("AI-сервер не настроен. Откройте настройки подключения.")
        provider = self.config.get("provider", "openai-compatible")
        if provider not in {"dokky", "dokky-test", "openai-compatible"}:
            raise RemoteAIError("Неизвестный провайдер AI-сервера")
        url = urlparse(base)
        if url.scheme not in {"http", "https"} or not url.hostname or url.username or url.password:
            raise RemoteAIError("Некорректный адрес AI-сервера")
        if provider == "dokky-test" and url.hostname not in {"localhost", "127.0.0.1", "::1"}:
            raise RemoteAIError("Тестовый Dokky разрешён только на локальном сервере")
        key = os.getenv(self.config.get("api_key_env", "KODDI_AI_API_KEY"), "")
        headers = {"Authorization": f"Bearer {key}"} if key and provider != "dokky-test" else {}
        if provider == "dokky-test":
            from koddi.desktop.transport import desktop_access

            if desktop_access.key and f"{url.scheme}://{url.netloc}" == desktop_access.origin:
                headers["X-Koddi-Desktop-Session"] = desktop_access.key
        return httpx.AsyncClient(
            base_url=base + "/",
            transport=self.transport,
            headers=headers,
            timeout=httpx.Timeout(connect=15, read=120, write=30, pool=15),
        )

    async def authorize(self, client):
        if self.config.get("provider") == "dokky":
            from koddi.dokky.client import dokky

            client.headers["Authorization"] = "Bearer " + await dokky.access_token()

    async def models(self) -> list[dict]:
        try:
            async with self._client() as client:
                await self.authorize(client)
                response = await client.get("models")
                self._check(response)
                data = response.json().get("data")
                if not isinstance(data, list) or any(
                    not isinstance(item, dict) or not isinstance(item.get("id"), str)
                    for item in data
                ):
                    raise ValueError
                return data
        except httpx.TransportError as exc:
            raise RemoteAIError("Не удалось получить модели AI-сервера") from exc
        except (ValueError, TypeError, AttributeError) as exc:
            raise RemoteAIError("Некорректный список моделей AI-сервера") from exc

    def _check(self, response: httpx.Response) -> None:
        if self.config.get("provider") == "dokky":
            from koddi.dokky.client import dokky

            dokky.check(response)
            return
        if response.is_error:
            # тело ответа не сохраняем: сервер мог вернуть в нём ключи или данные пользователя
            raise RemoteAIError(f"AI-сервер вернул HTTP {response.status_code}")

    async def stream(self, *, model: str, messages: list[dict], tools: list[dict], options=None):
        if self.config.get("provider") == "dokky" and (options or {}).get("_chat_id") is not None:
            from koddi.dokky.coding import session_operation, check_owner, ensure

            with session_operation(options["_chat_id"]) as identity:
                value = await ensure(options["_chat_id"])
                check_owner(identity)
                with session_operation(value["session"]["id"]):
                    iterator = self._stream(model=model, messages=messages, tools=tools, options=options, identity=identity)
                    try:
                        async for event in iterator:
                            check_owner(identity)
                            yield event
                    finally:
                        await iterator.aclose()
        else:
            identity = None
            if self.config.get("provider") == "dokky":
                from koddi.dokky.coding import owner

                identity = owner()
            iterator = self._stream(model=model, messages=messages, tools=tools, options=options, identity=identity)
            try:
                async for event in iterator:
                    if identity is not None:
                        from koddi.dokky.coding import check_owner

                        check_owner(identity)
                    yield event
            finally:
                await iterator.aclose()

    async def _stream(self, *, model: str, messages: list[dict], tools: list[dict], options=None, identity=None):
        options = deepcopy(options or {})
        if not model:
            raise RemoteAIError("Выберите модель AI-сервера")
        names = {
            item["function"]["name"].replace(".", "__"): item["function"]["name"] for item in tools
        }
        if len(names) != len(tools):
            raise RemoteAIError("Имена инструментов неоднозначны для AI-сервера")
        if self.config.get("provider") == "dokky" and len(tools) > 128:
            raise RemoteAIError(
                "Докки поддерживает до 128 инструментов. Отключите лишние MCP-подключения."
            )
        wire_tools = []
        for item in tools:
            function = deepcopy(item["function"])
            function["name"] = function["name"].replace(".", "__")
            wire_tools.append({"type": "function", "function": function})
        from koddi.agents.history import without_empty_assistants

        wire_messages = without_empty_assistants(messages)
        for message in wire_messages:
            if message.get("role") == "system" and isinstance(message.get("content"), str):
                for wire_name, internal_name in names.items():
                    message["content"] = message["content"].replace(internal_name, wire_name)
            for call in message.get("tool_calls", []):
                call["function"]["name"] = call["function"]["name"].replace(".", "__")
        body = {"model": model, "messages": wire_messages, "stream": True}
        coding = None
        chat_id = (options or {}).get("_chat_id")
        auxiliary = None
        if self.config.get("provider") == "dokky":
            from koddi.dokky.coding import ensure, prepare, auxiliary_session, canonical_messages, pending

            if chat_id is not None:
                coding = await ensure(chat_id)
                unresolved = pending(chat_id)
                if unresolved:
                    wire_messages = unresolved["messages"]
                    body = unresolved["body"]
                    wire_tools = body.get("tools", [])
                    coding["tools"] = wire_tools
                elif (coding["tools"] or coding["messages"]) and coding["tools"] != wire_tools:
                    # схемы и их порядок закреплены за сессией
                    raise RemoteAIError(
                        "Набор инструментов этого диалога изменился. Создайте новый чат."
                    )
                else:
                    coding["tools"] = wire_tools
                    checkpoint = (options or {}).get("_context_checkpoint") is True
                    wire_messages = canonical_messages(coding, wire_messages, checkpoint=checkpoint)
                    body = prepare(coding, wire_messages, wire_tools, checkpoint=checkpoint) | {"stream": True}
            else:
                auxiliary = await auxiliary_session()
                body.update(
                    model="dokky-code",
                    session_id=auxiliary["id"],
                    store=False,
                    reasoning_mode="light",
                )
        if wire_tools:
            body["tools"] = wire_tools
        for name in ("temperature", "top_p", "seed", "parallel_tool_calls", "tool_choice", "response_format", "stop", "max_tokens"):
            if name in (options or {}):
                body[name] = options[name]
        if self.config.get("provider") == "dokky":
            body["stream_options"] = {"include_usage": True}
            if len(wire_messages) > 4000 or len(json.dumps(body, ensure_ascii=False)) > 3_000_000:
                raise ContextLimitError(
                    "История требует сжатия перед отправкой; исходный журнал сохранён"
                )
        async with self._client() as client:
            if identity is not None:
                from koddi.dokky.coding import check_owner

                check_owner(identity)
            await self.authorize(client)
            if identity is not None:
                check_owner(identity)
            request_key = str(uuid.uuid4())
            if coding is not None:
                from koddi.dokky.coding import pending, save_pending

                unresolved = pending(chat_id)
                if unresolved:
                    body, wire_messages, request_key = (
                        unresolved["body"],
                        unresolved["messages"],
                        unresolved["key"],
                    )
                else:
                    save_pending(chat_id, request_key, body, wire_messages)
            attempt = -1
            repaired_empty = False
            recovered_history = False
            recovered_session = False
            encoded_body = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            while attempt < 3:
                attempt += 1
                if identity is not None:
                    check_owner(identity)
                received = False
                calls: dict[int, dict] = {}
                text, metadata, usage = "", {}, {}
                try:
                    from koddi.agents.telemetry import transport_attempt

                    transport_attempt(retry=attempt > 0)
                    async with client.stream(
                        "POST",
                        "chat/completions",
                        content=encoded_body,
                        headers={"Idempotency-Key": request_key, "Content-Type": "application/json"},
                    ) as response:
                        if (
                            self.config.get("provider") != "dokky"
                            and response.status_code in {429, 502, 503, 504}
                            and attempt < 3
                        ):
                            await asyncio.sleep(min(8, 2**attempt))
                            continue
                        if response.is_error:
                            await response.aread()
                        if identity is not None:
                            check_owner(identity)
                        self._check(response)
                        from koddi.logs import log_event

                        server_request_id = response.headers.get("x-request-id")
                        advertised_limit = response.headers.get("x-dokky-context-limit", "")
                        if advertised_limit.isdecimal() and int(advertised_limit) >= 4096:
                            self.config["context_tokens_limit"] = min(
                                min(CONTEXT_CEILING, 200_000) if self.config.get("provider") == "dokky" else CONTEXT_CEILING, int(advertised_limit)
                            )
                        finish_reason, done_marker = None, False
                        async for data in sse_payloads(response):
                            if data == "[DONE]":
                                done_marker = True
                                break
                            payload = json.loads(data)
                            if payload.get("error"):
                                if self.config.get("provider") == "dokky":
                                    from koddi.dokky.client import dokky

                                    error = payload["error"]
                                    if identity is not None:
                                        check_owner(identity)
                                    if error.get("code") == "session_revoked":
                                        async with dokky.lock:
                                            if identity is not None:
                                                check_owner(identity)
                                            # старый поток не отзывает токен, который уже обновился
                                            sent = client.headers.get("Authorization", "").removeprefix("Bearer ")
                                            if dokky.store.load().get("access_token") == sent:
                                                dokky.store.clear()
                                    from koddi.dokky.client import DokkyError

                                    raise DokkyError(
                                        error.get("message") or "Ошибка Докки",
                                        details=error,
                                        code=error.get("code", "upstream_unavailable"),
                                    )
                                raise RemoteAIError("AI-сервер сообщил об ошибке генерации")
                            if payload.get("dokky"):
                                from koddi.dokky.privacy import scrub_money

                                metadata = scrub_money(payload["dokky"])
                            if payload.get("usage"):
                                usage = payload["usage"]
                                advertised_limit = usage.get("context_tokens_limit")
                                if type(advertised_limit) is int and advertised_limit >= 4096:
                                    self.config["context_tokens_limit"] = min(
                                        min(CONTEXT_CEILING, 200_000) if self.config.get("provider") == "dokky" else CONTEXT_CEILING, advertised_limit
                                    )
                            for choice in payload.get("choices", []):
                                if choice.get("index", 0) != 0:
                                    continue
                                received = True
                                delta = choice.get("delta", {})
                                if finish_reason and (delta or choice.get("finish_reason")):
                                    raise RemoteAIError(
                                        "AI-сервер прислал данные после завершения ответа"
                                    )
                                if delta.get("refusal"):
                                    raise RemoteAIError("AI-сервер отказался выполнять запрос")
                                if delta.get("content"):
                                    if not isinstance(delta["content"], str):
                                        raise ValueError
                                    text += delta["content"]
                                    yield {"content": delta["content"]}
                                for part in delta.get("tool_calls", []):
                                    index = part["index"]
                                    if type(index) is not int or index < 0 or index > 127:
                                        raise ValueError
                                    call = calls.setdefault(
                                        index, {"id": "", "name": "", "arguments": ""}
                                    )
                                    call_id = part.get("id")
                                    if call_id:
                                        if not isinstance(call_id, str) or (
                                            call["id"] and call["id"] != call_id
                                        ):
                                            raise ValueError
                                        call["id"] = call_id
                                    function = part.get("function", {})
                                    call["name"] += function.get("name", "")
                                    call["arguments"] += function.get("arguments", "")
                                    if len(call["arguments"]) > 1_000_000:
                                        raise RemoteAIError("Слишком большие аргументы инструмента")
                                    yield {
                                        "tool_progress": {
                                            "id": call["id"],
                                            "name": names.get(call["name"], call["name"]),
                                            "bytes_received": len(
                                                call["arguments"].encode("utf-8")
                                            ),
                                        },
                                        "index": index,
                                    }
                                if choice.get("finish_reason"):
                                    if choice["finish_reason"] == "content_filter":
                                        raise RemoteAIError("Ответ остановлен фильтром AI-сервера")
                                    finish_reason = choice["finish_reason"]
                                    if finish_reason not in {"stop", "tool_calls", "length"}:
                                        raise RemoteAIError(
                                            "Неизвестная причина завершения AI-сервера"
                                        )
                        if not finish_reason or not received:
                            if self.config.get("provider") != "dokky":
                                raise RemoteAIError("Соединение прервалось до завершения ответа")
                            raise httpx.RemoteProtocolError("Незавершённый поток")
                        if self.config.get("provider") == "dokky" and (
                            not done_marker or not metadata
                        ):
                            raise httpx.RemoteProtocolError("Не получен итог Докки")
                        if finish_reason == "tool_calls" and not calls:
                            raise RemoteAIError("AI-сервер завершил вызов без инструмента")
                        empty = not text.strip() and not calls
                        log_event(
                            "WARNING" if empty else "INFO",
                            "ai.response",
                            "Завершение потока AI",
                            {
                                "client_request_id": request_key,
                                "server_request_id": server_request_id,
                                "attempt": attempt + 1,
                                "finish_reason": finish_reason,
                                "text_chars": len(text),
                                "tool_count": len(calls),
                                "empty": empty,
                            },
                        )
                        ids = [call["id"] for call in calls.values()]
                        if len(set(ids)) != len(ids) or any(
                            not call["id"] or not call["name"] for call in calls.values()
                        ):
                            raise RemoteAIError("Некорректные вызовы инструментов AI-сервера")
                        if coding is not None:
                            from koddi.dokky.coding import save, check_owner
                            from koddi.dokky.cache import record_cache

                            check_owner(identity)
                            try:
                                diagnostics = record_cache(coding["session"], metadata, usage, body["messages"])
                            except (ValueError, TypeError, OverflowError):
                                diagnostics = None

                            assistant = {"role": "assistant", "content": text or None}
                            if calls:
                                assistant["tool_calls"] = [
                                    {
                                        "id": call["id"],
                                        "type": "function",
                                        "function": {
                                            "name": call["name"],
                                            "arguments": call["arguments"],
                                        },
                                    }
                                    for _, call in sorted(calls.items())
                                ]
                            coding["messages"] = wire_messages + ([] if empty else [assistant])
                            coding["history"] = (
                                metadata.get("history")
                                if not empty and metadata.get("history_saved") is not False
                                else None
                            )
                            coding["session"].update(history=metadata.get("history", {}))
                            coding["session"]["last_usage"] = {"usage": usage, "dokky": metadata}
                            if metadata.get("replayed") is not True:
                                policy = coding["session"].setdefault("context_policy", {})
                                policy["steps_since_compaction"] = policy.get("steps_since_compaction", 0) + 1
                            save(chat_id, coding, finished_key=request_key, expected_owner=identity)
                            if diagnostics:
                                try:
                                    log_event(
                                        "WARNING" if diagnostics["status"] in {"miss", "partial"} else "INFO",
                                        "ai.cache", "Диагностика кэша AI", diagnostics,
                                    )
                                except Exception:
                                    # необязательный журнал не меняет уже сохранённый результат
                                    pass
                        if metadata or usage:
                            yield {"usage": usage, "dokky": metadata}
                        if empty:
                            raise RemoteAIError(
                                "AI-сервер завершил запрос без текста и действий. Автоматический новый платный запрос не отправлен. История сохранена; можно отправить новый запрос.",
                                code="empty_response",
                                request_id=server_request_id,
                            )
                        if auxiliary:
                            from koddi.dokky.client import dokky

                            try:
                                await dokky.request(
                                    "DELETE",
                                    "/coding/sessions/" + auxiliary["id"],
                                    params={"confirm": "true"},
                                )
                            except Exception:
                                pass
                        # обрезанный вызов не исполняем, даже если его JSON выглядит полным
                        if finish_reason == "length":
                            yield {"finish_reason": finish_reason}
                            return
                        ids = [call["id"] for call in calls.values()]
                        if len(set(ids)) != len(ids) or any(
                            not call["id"] or not call["name"] for call in calls.values()
                        ):
                            raise RemoteAIError("Некорректные вызовы инструментов AI-сервера")
                        yield {"finish_reason": finish_reason}
                        for index, call in sorted(calls.items()):
                            yield {
                                "tool_call": {
                                    **call,
                                    "name": names.get(call["name"], call["name"]),
                                    "index": index,
                                }
                            }
                        return
                except (ValueError, TypeError, KeyError, AttributeError) as exc:
                    raise RemoteAIError("Некорректный потоковый ответ AI-сервера") from exc
                except (httpx.TransportError, httpx.TimeoutException) as exc:
                    if attempt == 3 or (received and self.config.get("provider") != "dokky"):
                        raise RemoteAIError(
                            "Соединение с AI-сервером прервано. Повторите запрос."
                        ) from exc
                    if received:
                        yield {"reset": True}
                    await asyncio.sleep(2**attempt)
                except Exception as exc:
                    from koddi.dokky.client import DokkyError

                    if not isinstance(exc, DokkyError):
                        raise
                    if (
                        exc.status == 422
                        and exc.code == "validation_error"
                        and "пустое сообщение" in str(exc).lower()
                    ):
                        cleaned = without_empty_assistants(wire_messages)
                        if cleaned != wire_messages and not repaired_empty:
                            # 422 отклонён до вызова модели; старый неопределённый запрос сначала проверен тем же ключом.
                            wire_messages = cleaned
                            body.pop("history", None)
                            body["messages"] = wire_messages
                            request_key = str(uuid.uuid4())
                            encoded_body = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
                            if coding is not None:
                                save_pending(chat_id, request_key, body, wire_messages)
                            repaired_empty = True
                            attempt = -1
                            continue
                    if exc.status == 422 and exc.code in {
                        "validation_error",
                        "context_length_exceeded",
                    }:
                        match = re.search(r"превышает лимит\s+(\d+)", str(exc))
                        if match or exc.code == "context_length_exceeded":
                            if chat_id is not None:
                                from koddi.storage.db import db

                                with db() as conn:
                                    conn.execute(
                                        "DELETE FROM coding_pending WHERE chat_id=? AND request_key=?",
                                        (chat_id, request_key),
                                    )
                            limit = int(match[1]) if match else None
                            raise ContextLimitError(
                                "Контекст требует сжатия; исходная история сохранена", limit
                            ) from exc
                    if exc.code == "history_mismatch" and "history" in body and coding is not None and not recovered_history:
                        from koddi.dokky.coding import recover, rebase_pending, prepare, discard_pending, check_owner, isolate_recovered_journal

                        check_owner(identity)
                        discard_pending(chat_id, request_key)
                        previous = coding["messages"]
                        coding = await recover(chat_id)
                        recovered_history = True
                        try:
                            wire_messages = rebase_pending(previous, wire_messages, coding["messages"])
                        except DokkyError:
                            isolate_recovered_journal(chat_id, coding)
                            raise
                        body = body | prepare(coding, wire_messages, wire_tools)
                        request_key = str(uuid.uuid4())
                        encoded_body = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
                        save_pending(chat_id, request_key, body, wire_messages)
                        attempt = -1
                        continue
                    if exc.code == "not_found" and coding is not None and not recovered_session:
                        from koddi.storage.db import db

                        with db() as conn:
                            conn.execute("DELETE FROM coding_pending WHERE chat_id=? AND request_key=?", (chat_id, request_key))
                        from koddi.dokky.context import transition
                        from koddi.dokky.coding import load, prepare

                        async for _ in transition(chat_id, action="expired_restore", messages=wire_messages, tools=wire_tools, locked=True):
                            pass
                        coding = load(chat_id)
                        wire_messages = coding["messages"]
                        body.pop("history", None)
                        body = body | prepare(coding, wire_messages, wire_tools)
                        request_key = str(uuid.uuid4())
                        encoded_body = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
                        save_pending(chat_id, request_key, body, wire_messages)
                        recovered_session = True
                        attempt = -1
                        continue
                    if exc.code == "invalid_token" and attempt == 0:
                        from koddi.dokky.client import dokky, LEGACY_CLIENT_ID

                        async with dokky.lock:
                            if identity is not None:
                                check_owner(identity)
                            value = dokky.store.load()
                            if not value:
                                raise
                            sent = client.headers.get("Authorization", "").removeprefix("Bearer ")
                            # параллельный запрос мог уже обновить токен того же аккаунта
                            if value.get("access_token") == sent:
                                await dokky.tokens(
                                    {
                                        "grant_type": "refresh_token",
                                        "refresh_token": value["refresh_token"],
                                    },
                                    client_id=value.get("client_id") or LEGACY_CLIENT_ID,
                                )
                        await self.authorize(client)
                        if received:
                            yield {"reset": True}
                        continue
                    if not exc.retryable or attempt == 3:
                        if not exc.retryable and chat_id is not None:
                            from koddi.storage.db import db

                            with db() as conn:
                                conn.execute(
                                    "DELETE FROM coding_pending WHERE chat_id=? AND request_key=?",
                                    (chat_id, request_key),
                                )
                        raise
                    if received:
                        yield {"reset": True}
                    await asyncio.sleep(max(float(exc.retry_after), 2**attempt))
            raise RemoteAIError(
                "Восстановление запроса не завершилось. История и ожидающий запрос сохранены для продолжения."
            )
