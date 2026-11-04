import asyncio

import httpx

from koddi.dokky.client import DokkyClient


def test_shared_client_works_directly_and_in_async_with_without_closing():
    async def scenario():
        client = DokkyClient()
        first = client.http()
        request = first.build_request("GET", "https://example.org/x", headers={"X-Test": "1"})
        assert request.headers["X-Test"] == "1"
        await first.aclose()  # закрытие не должно рвать общие соединения
        async with client.http() as shared:
            assert isinstance(shared, httpx.AsyncClient)
            assert not shared.is_closed
            again = client.http()
            assert again._client is shared

    asyncio.run(scenario())


def test_warm_connections_stay_quiet_without_login_and_on_network_failure(monkeypatch):
    client = DokkyClient()
    asked = []

    class Store:
        def __init__(self, session):
            self.session = session

        def load(self):
            return self.session

    class Pool:
        async def get(self, url, **_kwargs):
            asked.append(url)
            raise httpx.ConnectError("нет сети")

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc):
            return False

    monkeypatch.setattr(client, "http", Pool)
    client.store = Store({})
    asyncio.run(client.warm_connections())
    assert asked == []

    client.store = Store({"access_token": "x"})
    asyncio.run(client.warm_connections(count=2))
    # в прогреве только публичный адрес и никаких учётных данных
    assert len(asked) == 2 and all(url.endswith("/api/frontend-release") for url in asked)
