import json

import httpx
import pytest

from koddi.dokky.client import DokkyClient
from koddi.dokky.privacy import scrub_event_line, scrub_money


def test_money_keys_are_removed_everywhere_but_percentages_stay():
    value = {
        "dokky": {
            "billing": {"charged_rub": "0.0214", "remaining_percent": 91, "window_used_percent": 4},
            "usage": {"cached_tokens": 10},
        },
        "options": [{"action": "switch", "usd": 0.0125, "tokens_after": 5}],
        "stats": {"requests": 3, "charged_rub": "1.5", "price": 1},
    }
    clean = scrub_money(value)
    assert clean["dokky"]["billing"] == {"remaining_percent": 91, "window_used_percent": 4}
    assert clean["options"] == [{"action": "switch", "tokens_after": 5}]
    assert clean["stats"] == {"requests": 3}
    assert value["dokky"]["billing"]["charged_rub"] == "0.0214"


def test_event_lines_are_scrubbed_and_other_lines_pass_through():
    line = 'data: {"dokky":{"billing":{"charged_rub":"0.1","remaining_percent":9}}}'
    assert json.loads(scrub_event_line(line)[5:]) == {"dokky": {"billing": {"remaining_percent": 9}}}
    for other in ("event: turn.completed", ": keepalive", "data: [DONE]", "data: не json", ""):
        assert scrub_event_line(other) == other


class Store:
    def load(self):
        return {"access_token": "a", "refresh_token": "r", "expires_at": 4_000_000_000, "user": {"id": "u"}}

    def save(self, value):
        pass

    def clear(self):
        pass


@pytest.mark.asyncio
async def test_client_never_returns_money_from_server_responses(monkeypatch):
    from koddi.dokky import client as module

    monkeypatch.setattr(module, "account_identity", lambda: "u")

    def handler(request):
        return httpx.Response(
            200,
            json={
                "recommended": "switch",
                "options": [{"action": "switch", "usd": 0.01, "tokens_after": 7}],
                "dokky": {"billing": {"charged_rub": "0.5", "remaining_percent": 80}},
            },
        )

    client = DokkyClient(transport=httpx.MockTransport(handler), store=Store())
    monkeypatch.setattr(client, "access_token", lambda: _token())
    value = (await client.request("GET", "/coding/sessions/cs_x/switch-plan")).json()
    text = json.dumps(value)
    assert "usd" not in text and "charged_rub" not in text and "0.5" not in text
    assert value["dokky"]["billing"] == {"remaining_percent": 80}


async def _token():
    return "a"
