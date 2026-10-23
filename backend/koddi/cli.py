from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from koddi.agents.orchestrator import AgentOrchestrator
from koddi.projects import add_project
from koddi.storage import init_db
from koddi.storage.db import db


async def run(args) -> int:
    init_db()
    project = add_project(str(Path(args.workspace).resolve())) if args.workspace else None
    with db() as conn:
        chat_id = int(
            conn.execute(
                "INSERT INTO chats(project_id,title,mode,model) VALUES (?,'Новая задача',?,?)",
                (project["id"] if project else None, args.mode, args.model),
            ).lastrowid
        )
    async for event in AgentOrchestrator().run(
        chat_id=chat_id,
        project_id=project["id"] if project else None,
        workspace=Path(project["path"]) if project else None,
        user_message=args.prompt,
        mode=args.mode,
        model=args.model,
    ):
        kind = event.splitlines()[0][7:]
        payload = json.loads(event.split("data: ", 1)[1])
        if args.json:
            print(json.dumps({"event": kind, "data": payload}, ensure_ascii=False), flush=True)
        elif kind == "message.delta":
            print(payload["content"], end="", flush=True)
        elif kind == "tool.call":
            print(f"\nДействие: {payload['name']}", flush=True)
        elif kind == "turn.failed":
            print(f"\nОшибка: {payload['error']}", flush=True)
        if kind == "agent.input_required":
            if args.non_interactive:
                value = (
                    {"approved": False}
                    if payload["kind"] == "approval"
                    else {
                        "answer": "Автоматический запуск: уточнение невозможно. Отметьте задачу заблокированной."
                    }
                )
            elif payload["kind"] == "approval":
                answer = await asyncio.to_thread(
                    input, f"\nРазрешить {payload.get('name')}? [да/нет]: "
                )
                value = {"approved": answer.strip().lower() == "да"}
            else:
                value = {
                    "answer": await asyncio.to_thread(input, "\n" + payload["question"] + ": ")
                }
            with db() as conn:
                conn.execute(
                    "UPDATE agent_requests SET response_json=? WHERE id=?",
                    (json.dumps(value, ensure_ascii=False), payload["id"]),
                )
    with db() as conn:
        status = conn.execute(
            "SELECT status FROM turns WHERE chat_id=? ORDER BY id DESC LIMIT 1", (chat_id,)
        ).fetchone()[0]
    return 0 if status == "completed" else 1


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Докки Code: серверный агент разработки", add_help=False
    )
    parser._positionals.title = "Аргументы"
    parser._optionals.title = "Параметры"
    parser.add_argument("-h", "--help", action="help", help="Показать справку")
    parser.add_argument("prompt", help="Задача для агента")
    parser.add_argument("--workspace", help="Папка проекта")
    parser.add_argument("--model", help="Идентификатор серверной модели")
    parser.add_argument("--mode", default="development", help="Режим агента")
    parser.add_argument("--json", action="store_true", help="Поток событий JSON")
    parser.add_argument(
        "--non-interactive", action="store_true", help="Без вопросов; разрешения отклоняются"
    )
    raise SystemExit(asyncio.run(run(parser.parse_args())))


if __name__ == "__main__":
    main()
