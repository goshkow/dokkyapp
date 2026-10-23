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
