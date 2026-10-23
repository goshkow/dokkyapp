from __future__ import annotations

import asyncio
import json

from koddi.dokky.client import API, DokkyError, dokky


async def stop_local_runs(*, notify=True, only_if_signed_out=False, expected_session=None):
    from koddi.agents import agent_runs
    from koddi.events import event_bus

    def current():
        value = dokky.store.load()
        return not (only_if_signed_out and value) and (
            expected_session is None
            or value.get("access_token") == expected_session.get("access_token")
        )

    if not current():
        return False
    from koddi.automations.service import cancel_all

    cancel_all()
    from koddi.agents.queue import request_queue

    await request_queue.shutdown()
    if not current():
        return False
    for chat_id in agent_runs.active_ids():
        await agent_runs.cancel(chat_id)
    if not current():
        return False
    agent_runs.forget_completed()
    from koddi.browser.service import browser_close_all

    await asyncio.to_thread(browser_close_all)
    if not current():
        return False
    from koddi.dokky.coding import clear_local_account_data

    clear_local_account_data()
    if notify:
        event_bus.revoke()
    else:
        event_bus.revoke('dokky.session_changed')
    return True


async def watch_account(client=None, on_revoked=None):
    client = client or dokky
    if on_revoked is None:

        async def on_revoked():
            # старый выход не должен прерывать уже начатый вход в браузере
            login_pending = bool(client.pending) or client.lock.locked()
            await stop_local_runs(only_if_signed_out=True, notify=not login_pending)

    had_session = False
    cursor = "0"
    while True:
        if not client.store.load():
            if had_session:
                had_session = False
                await on_revoked()
            await asyncio.sleep(1)
            continue
        had_session = True
        try:
            token = await client.access_token()
            async with client.http() as http:
                async with http.stream(
                    "GET",
                    API + "/events",
                    params={"after": cursor},
                    headers={"Authorization": "Bearer " + token, "Accept": "text/event-stream"},
                ) as response:
                    if client.store.load().get("access_token") != token:
                        cursor = "0"
                        continue
                    if response.is_error:
                        await response.aread()
                    client.check(response)
                    kind = ""
                    async for line in response.aiter_lines():
                        if client.store.load().get("access_token") != token:
                            cursor = "0"
                            break
                        if line.startswith("id:"):
                            value = line[3:].strip()
                            if value.isdigit():
                                cursor = value
                        if line.startswith("event:"):
                            kind = line[6:].strip()
                        if line == "event: auth.revoked":
                            client.store.clear()
                            await on_revoked()
                            had_session = False
                            cursor = "0"
                            break
                        if line.startswith("data:") and kind in {
                            "chat.updated",
                            "chat.deleted",
                            "quota.updated",
                            "profile.updated",
                        }:
                            from koddi.events import event_bus

                            value = json.loads(line[5:])
                            await event_bus.publish("dokky." + kind, value)
        except DokkyError as exc:
            if exc.status == 401 and not client.store.load():
                await on_revoked()
        except Exception:
            pass
        await asyncio.sleep(3)
