"""Подставной HTTP-сервер для тестов, не настоящий AI. Подключается через create_mock_app() или запуском модуля."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field


MODEL_ID = "dokky-test-scripted"
DEMO_COMMAND = "/dokky-demo read-create-edit-verify"
CREATE_COMMAND = "Создай скрипт приветствия и проверь его"
EDIT_COMMAND = "Добавь обработку пустого ввода"
PLAN_COMMAND = "/dokky-demo plan-greeting"
PLAN_MARKER = "Test fixture: greeting-plan-v1"
NOTICE = "Тестовый API Докки: детерминированный сценарий, не нейросетевая генерация."
BASE_CODE = (
    "def greet(value: str) -> str:\n"
    '    return "Привет!" if value.strip().lower() == "привет" else ""\n'
)
EDITED_CODE = (
    "def greet(value: str) -> str:\n"
    "    if not value.strip():\n"
    '        return "Введите приветствие."\n'
    '    return "Привет!" if value.strip().lower() == "привет" else ""\n'
)


class CompletionRequest(BaseModel):
    model: str
    messages: list[dict[str, Any]]
    tools: list[dict[str, Any]] = Field(default_factory=list)
    stream: bool = True


def _event(delta: dict | None = None, finish: str | None = None) -> str:
    return (
        "data: "
        + json.dumps(
            {"choices": [{"index": 0, "delta": delta or {}, "finish_reason": finish}]},
            ensure_ascii=False,
        )
        + "\n\n"
    )


def _text(value: str) -> list[str]:
    return [_event({"content": value}), _event(finish="stop"), "data: [DONE]\n\n"]


def _call(name: str, arguments: dict, call_id: str, available: set[str]) -> list[str]:
    wire_name = name.replace(".", "__")
    if wire_name not in available:
        return _text(
            NOTICE + "\nДля этого демосценария нет необходимых инструментов в активном режиме."
        )
    encoded = json.dumps(arguments, ensure_ascii=False)
    middle = max(1, len(encoded) // 2)
    return [
        _event(
            {
                "tool_calls": [
                    {
                        "index": 0,
                        "id": call_id,
                        "type": "function",
                        "function": {"name": wire_name, "arguments": encoded[:middle]},
                    }
                ]
            }
        ),
        _event({"tool_calls": [{"index": 0, "function": {"arguments": encoded[middle:]}}]}),
        _event(finish="tool_calls"),
        "data: [DONE]\n\n",
    ]


def _plan(status: str) -> dict:
    return {"steps": [{"title": "Выполнить явный демонстрационный сценарий", "status": status}]}


def _scenario(body: CompletionRequest) -> list[str]:
    available = {item["function"]["name"] for item in body.tools}
    system = body.messages[0].get("content", "") if body.messages else ""
    if isinstance(system, str) and system.startswith("Generate only a concise chat title"):
        return _text("Тестовый сценарий Докки")
    if isinstance(system, str) and system.startswith("Summarize this task history"):
        return _text(
            NOTICE
            + " Продолжение явного демосценария по последнему подтверждённому вызову инструмента."
        )
    user_index = next(
        (
            i
            for i in range(len(body.messages) - 1, -1, -1)
            if body.messages[i].get("role") == "user"
        ),
        None,
    )
    command = body.messages[user_index].get("content") if user_index is not None else None
    if (
        isinstance(command, str)
        and command.startswith("Незавершённый план предыдущего запуска:")
        and user_index
        and body.messages[user_index - 1].get("role") == "user"
    ):
        previous = body.messages[user_index - 1].get("content", "")
        if (
            previous.startswith("Implement the following user-approved plan")
            and PLAN_MARKER in previous
        ):
            user_index -= 1
            command = previous
    if command == PLAN_COMMAND:
        completed = any(
            message.get("tool_call_id") == "dokky_plan_only"
            for message in body.messages[user_index + 1 :]
        )
        if completed:
            return _call(
                "task.complete",
                {
                    "summary": NOTICE
                    + "\n"
                    + PLAN_MARKER
                    + "\nСоздать greeting.py с функцией greet, прочитать файл и проверить синтаксис."
                },
                "dokky_plan_done",
                available,
            )
        return _call(
            "plan.update",
            {
                "document": "# План скрипта приветствия\n\n"
                + PLAN_MARKER
                + "\n\n## Цель\nСоздать greeting.py с функцией greet.\n\n"
                + "## Реализация\nФункция принимает строку и возвращает приветствие. "
                + "Изменяется только greeting.py.\n\n"
                + "## Критерии приёмки\nФайл существует, содержит функцию greet, "
                + "ответ на привет корректен.\n\n"
                + "## Проверки\nПрочитать созданный файл и проверить синтаксис.\n\n"
                + "## Границы\nНе устанавливать зависимости и не менять другие файлы.",
                "steps": [
                    {"title": "Создать greeting.py и проверить синтаксис", "status": "pending"}
                ]
            },
            "dokky_plan_only",
            available,
        )
    if (
        isinstance(command, str)
        and command.startswith("Implement the following user-approved plan")
        and PLAN_MARKER in command
    ):
        command = CREATE_COMMAND
    if command not in {DEMO_COMMAND, CREATE_COMMAND, EDIT_COMMAND}:
        return _text(
            NOTICE
            + "\nПроизвольные задачи не исполняются. Доступны только явные демокоманды: "
            + CREATE_COMMAND
            + "; "
            + EDIT_COMMAND
            + "; "
            + DEMO_COMMAND
            + "."
        )
    path = "dokky_demo.py" if command == DEMO_COMMAND else "greeting.py"
    operations = [("plan.update", _plan("running"))]
    if command == EDIT_COMMAND:
        operations += [("file.read", {"path": path, "max_bytes": 8192})]
    else:
        operations += [
            ("directory.list", {"path": "."}),
            ("file.create", {"path": path, "content": BASE_CODE}),
            ("file.read", {"path": path, "max_bytes": 8192}),
        ]
    if command != CREATE_COMMAND:
        operations += [
            (
                "file.write_patch",
                {
                    "path": path,
                    "content": EDITED_CODE,
                    "description": "Тестовый Докки: обработка пустого ввода",
                },
            ),
            ("file.read", {"path": path, "max_bytes": 8192}),
        ]
    operations += [
        ("verification.run", {"paths": [path]}),
        ("plan.update", _plan("completed")),
        (
            "task.complete",
            {
                "summary": NOTICE
                + f"\nФайл {path} прочитан и проверен инструментом verification.run. "
                "Это проверка синтаксиса, не полноценный запуск поведенческих тестов. "
                "Для использования импортируйте функцию greet."
            },
        ),
    ]
    stage = 0
    blocked = None
    for message in body.messages[user_index + 1 :]:
        if message.get("role") != "tool":
            continue
        call_id = message.get("tool_call_id", "")
        if call_id == "dokky_blocked":
            return _call(
                "task.complete",
                {
                    "summary": NOTICE
                    + "\nДемосценарий заблокирован. Изменения не продолжались; проверьте результат предыдущего инструмента."
                },
                "dokky_blocked_complete",
                available,
            )
        if not call_id.startswith("dokky_step_"):
            continue
        try:
            index = int(call_id.removeprefix("dokky_step_"))
            name, _ = operations[index]
            result = json.loads(message["content"])
        except (ValueError, KeyError, IndexError, TypeError):
            blocked = "Некорректный результат инструмента"
            break
        if result.get("ok") is not True:
            blocked = "Инструмент сообщил об ошибке или запрете"
            break
        if name == "directory.list" and (
            result.get("truncated")
            or any(item.get("path") == path for item in result.get("items", []))
        ):
            blocked = "Демофайл уже существует или список неполон; перезапись запрещена"
            break
        if name == "file.read":
            preceding_edit = any(n == "file.write_patch" for n, _ in operations[:index])
            expected = EDITED_CODE if preceding_edit else BASE_CODE
            if result.get("truncated") or result.get("content") != expected:
                blocked = "Содержимое отличается от ожидаемого демофайла; редактирование запрещено"
                break
        stage = max(stage, index + 1)
    if blocked:
        arguments = {"steps": [{"title": blocked, "status": "blocked"}]}
        return _call("plan.update", arguments, "dokky_blocked", available)
    if stage >= len(operations):
        return _text(NOTICE + "\nДемосценарий завершён.")
    name, arguments = operations[stage]
    frames = _call(name, arguments, f"dokky_step_{stage}", available)
    if stage == 0:
        intro = (
            NOTICE
            + "\n"
            + (
                "Прочитаю greeting.py, добавлю обработку пустого ввода и проверю результат."
                if command == EDIT_COMMAND
                else f"Изучу папку, создам {path} и проверю результат."
            )
        )
        pieces = [intro[i : i + 18] for i in range(0, len(intro), 18)]
        frames = [_event({"content": part}) for part in pieces] + frames
    return frames


def create_mock_app(*, chunk_delay: float = 0.005) -> FastAPI:
    """Монтируется на /api/test-dokky, base_url получается .../api/test-dokky/v1.

    Доступа к рабочей папке, оболочки, загрузки моделей и исходящих запросов нет.
    provider=dokky-test включает и монтирует сам API.
    """
    if not 0 <= chunk_delay <= 10:
        raise ValueError("Задержка фрагмента должна быть от 0 до 10 секунд")
    app = FastAPI(title="Тестовый API Докки (не нейросеть)")
    app.state.provider = "dokky-test"
    app.state.is_test_provider = True

    @app.get("/health")
    async def health():
        return {"ok": True, "provider": "dokky-test", "test_only": True, "notice": NOTICE}

    @app.get("/v1/models")
    async def models():
        return {
            "object": "list",
            "data": [
                {
                    "id": MODEL_ID,
                    "name": "Докки-тест",
                    "object": "model",
                    "owned_by": "koddi-test-only",
                    "test_only": True,
                }
            ],
        }

    @app.post("/v1/chat/completions")
    async def completion(body: CompletionRequest):
        if body.model != MODEL_ID:
            raise HTTPException(404, "Доступна только тестовая модель dokky-test-scripted")
        if not body.stream:
            raise HTTPException(422, "Тестовый сервер поддерживает только stream=true")
        try:
            frames = _scenario(body)
        except (ValueError, TypeError, KeyError, AttributeError):
            raise HTTPException(422, "Некорректный тестовый запрос") from None

        async def generate():
            for frame in frames:
                await asyncio.sleep(chunk_delay)
                yield frame

        return StreamingResponse(
            generate(),
            media_type="text/event-stream",
            headers={"X-Koddi-Test-Provider": "true", "Cache-Control": "no-cache"},
        )

    return app


def main():
    import argparse
    import uvicorn

    parser = argparse.ArgumentParser(description="Локальный тестовый Dokky API, не AI")
    parser.add_argument("--port", type=int, default=8766)
    args = parser.parse_args()
    uvicorn.run(create_mock_app(), host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
