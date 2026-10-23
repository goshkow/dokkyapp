"""изолированная проверка UI очереди и отмены без запросов к AI."""

from __future__ import annotations

import asyncio
import json
import os
import socket
import tempfile
import threading
import time
from pathlib import Path

os.environ["KODDI_DATA_DIR"] = tempfile.mkdtemp(prefix="koddi-queue-ui-")
os.environ["KODDI_INTERNAL_TEST"] = "1"

import httpx
import uvicorn
from playwright.sync_api import sync_playwright

from koddi.agents.orchestrator import AgentOrchestrator, sse
from koddi.api import create_app
from koddi.storage.db import db


async def fixture_turn(self, *, chat_id, user_message, **kwargs):
    with db() as conn:
        turn = conn.execute(
            "INSERT INTO turns(chat_id,status) VALUES (?,'running')", (chat_id,)
        ).lastrowid
        conn.execute(
            "INSERT INTO messages(chat_id,role,content,metadata_json) VALUES (?,'user',?,?)",
            (chat_id, user_message, json.dumps({"turn_id": turn})),
        )
    yield sse("turn.started", {"turn_id": turn, "chat_id": chat_id})
    yield sse("agent.status", {"text": "Тест очереди, без AI"})
    try:
        await asyncio.sleep(2)
        with db() as conn:
            conn.execute(
                "UPDATE turns SET status='completed',completed_at=CURRENT_TIMESTAMP WHERE id=?",
                (turn,),
            )
            conn.execute(
                "INSERT INTO messages(chat_id,role,content,metadata_json) VALUES (?,'assistant',?,?)",
                (
                    chat_id,
                    "Ответ: " + user_message,
                    json.dumps({"turn_id": turn, "status": "completed", "channel": "final"}),
                ),
            )
        yield sse("turn.completed", {"turn_id": turn})
    except asyncio.CancelledError:
        with db() as conn:
            conn.execute(
                "UPDATE turns SET status='cancelled',completed_at=CURRENT_TIMESTAMP WHERE id=?",
                (turn,),
            )
        raise


def main():
    AgentOrchestrator.run = fixture_turn
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    server = uvicorn.Server(
        uvicorn.Config(create_app(), host="127.0.0.1", port=port, log_level="error")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 15
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.05)
    base = f"http://127.0.0.1:{port}"
    httpx.post(base + "/api/ai/test-enable").raise_for_status()
    output = Path(tempfile.mkdtemp(prefix="koddi-queue-view-"))
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 900}, bypass_csp=True)
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(base + "/ui/")
            page.wait_for_function(
                "!document.body.classList.contains('booting')&&document.querySelector('#model').value"
            )
            page.evaluate("document.querySelector('#mode').value='chat'")
            page.locator("#prompt").fill("Первый запрос")
            page.locator("#send").click()
            page.wait_for_function("state.running")
            page.locator("#prompt").fill("Второй запрос")
            page.locator("#prompt").press("Enter")
            page.locator("#local-queue").get_by_text("Второй запрос", exact=True).wait_for()
            page.wait_for_function(
                "document.querySelector('#feed').textContent.includes('Ответ: Второй запрос')&&!state.running",
                timeout=15000,
            )
            chat = page.evaluate("state.chat.id")
            messages = httpx.get(base + f"/api/chats/{chat}/messages").json()
            assert [item["content"] for item in messages if item["role"] == "user"] == [
                "Первый запрос",
                "Второй запрос",
            ]
            page.locator("#prompt").fill("Останавливаемый")
            page.locator("#send").click()
            page.wait_for_function("state.running")
            page.locator("#prompt").fill("Сохранить в очереди")
            page.locator("#prompt").press("Enter")
            page.locator("#local-queue").get_by_text("Сохранить в очереди", exact=True).wait_for()
            page.locator("#send").click()
            page.wait_for_function("!state.running")
            queue = httpx.get(base + f"/api/chats/{chat}/queue").json()
            assert queue["paused"] and any(
                item["content"] == "Сохранить в очереди" and item["status"] == "queued"
                for item in queue["items"]
            )
            page.locator("#local-queue").get_by_text(
                "Очередь приостановлена", exact=True
            ).wait_for()
            page.screenshot(path=str(output / "queue-paused.png"))
            assert not errors, errors
            browser.close()
        print(output)
    finally:
        server.should_exit = True
        thread.join(timeout=10)


if __name__ == "__main__":
    main()
