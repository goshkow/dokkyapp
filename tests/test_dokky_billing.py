import json

import httpx
import pytest
from fastapi.testclient import TestClient

import koddi.storage.db as storage
from koddi.api import create_app
from koddi.dokky.client import API, ORIGIN, DokkyClient
from koddi.dokky.privacy import PAID_PRICE_KEYS, scrub_money

PLANS = {
    "catalog_version": "abc",
    "plans": [{
        "id": "super", "name": "Plus", "action": "subscribe",
        "price": {"amount": 300.0, "original_amount": 300.0, "currency": "RUB"},
        "checkout_price": {"amount": 300.0, "currency": "RUB"},
        "features": [{"id": "usage", "label": "×3 объём использования"}],
    }],
    "usage_reset": {"available": True, "options": [{"percent": 25, "price": 90.0, "remaining_after_percent": 32}]},
    "dokky": {"billing": {"charged_rub": "0.5", "remaining_percent": 7}},
}


class Store:
    def load(self):
        return {"access_token": "a", "refresh_token": "r", "expires_at": 4_000_000_000, "user": {"id": "u"}}

    def save(self, value):
        pass

    def clear(self):
        pass


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DB_PATH", tmp_path / "billing.sqlite3")
    storage.init_db()


def test_plan_prices_survive_only_where_the_person_pays():
    kept = scrub_money(PLANS, PAID_PRICE_KEYS)
    assert kept["plans"][0]["price"]["amount"] == 300.0 and kept["plans"][0]["checkout_price"]["amount"] == 300.0
    assert kept["usage_reset"]["options"][0]["price"] == 90.0
    # расход и списания вырезаются даже рядом с ценами
    assert "charged_rub" not in json.dumps(kept) and kept["dokky"]["billing"] == {"remaining_percent": 7}
    # без исключения вырезается всё, как раньше
    assert "price" not in json.dumps(scrub_money(PLANS))


@pytest.mark.asyncio
async def test_billing_and_subscription_keep_prices_but_chat_traffic_does_not(monkeypatch):
    from koddi.dokky import client as module

    monkeypatch.setattr(module, "account_identity", lambda: "u")
    client = DokkyClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=PLANS)), store=Store())

    async def token():
        return "a"

    monkeypatch.setattr(client, "access_token", token)
    billing = (await client.request("GET", "/billing/plans")).json()
    assert billing["plans"][0]["price"]["amount"] == 300.0 and "charged_rub" not in json.dumps(billing)
    assert (await client.request("GET", "/subscription")).json()["usage_reset"]["options"][0]["price"] == 90.0
    other = (await client.request("GET", "/coding/sessions/cs_x/switch-plan")).json()
    assert "price" not in json.dumps(other)


@pytest.fixture
def proxy(monkeypatch):
    """Локальный сервер приложения; запросы к Докки записываются и возвращают заданный ответ."""
    sent = []
    state = {"answer": httpx.Response(201, json={"id": "bl_1", "url": ORIGIN + "/pay/AbC", "status": "created"})}

    async def request(method, path, **kwargs):
        sent.append((method, path, kwargs.get("json")))
        return state["answer"]

    from koddi.dokky.client import dokky

    monkeypatch.setattr(dokky, "request", request)
    return TestClient(create_app()), sent, state


def test_payment_link_request_passes_only_known_fields(proxy):
    client, sent, _ = proxy
    answer = client.post("/api/dokky/cloud/billing/links", json={"intent": "plan", "plan": "ultra", "return_app": "koddi"})
    assert answer.status_code == 201 and answer.json()["url"].startswith(ORIGIN + "/pay/")
    assert sent == [("POST", "/billing/links", {"intent": "plan", "plan": "ultra", "return_app": "koddi"})]
    sent.clear()
    client.post("/api/dokky/cloud/billing/links", json={"intent": "topup", "percent": 50})
    assert sent == [("POST", "/billing/links", {"intent": "topup", "percent": 50})]


@pytest.mark.parametrize("body", [
    {},
    {"intent": "buy"},
    {"intent": "plan"},
    {"intent": "plan", "plan": "../x"},
    {"intent": "plan", "plan": "ultra", "amount": 1},
    {"intent": "topup", "plan": "ultra"},
    {"intent": "topup", "percent": 10},
    {"intent": "topup", "percent": True},
    {"intent": "topup", "return_app": "evil"},
])
def test_payment_link_request_rejects_bad_bodies_before_the_network(proxy, body):
    client, sent, _ = proxy
    assert client.post("/api/dokky/cloud/billing/links", json=body).status_code == 422
    assert sent == []


def test_payment_link_must_lead_to_the_dokky_pay_page(proxy):
    client, _, state = proxy
    state["answer"] = httpx.Response(201, json={"id": "bl_1", "url": "https://evil.example/pay/AbC"})
    answer = client.post("/api/dokky/cloud/billing/links", json={"intent": "plan", "plan": "ultra"})
    assert answer.status_code == 502 and answer.json()["detail"]["code"] == "bad_payment_link"
    state["answer"] = httpx.Response(201, json={"id": "bl_1", "url": ORIGIN + ".evil.example/pay/x"})
    assert client.post("/api/dokky/cloud/billing/links", json={"intent": "plan", "plan": "ultra"}).status_code == 502


def test_only_catalog_status_and_link_routes_are_open(proxy):
    client, sent, state = proxy
    state["answer"] = httpx.Response(200, json={"status": "paid"})
    assert client.get("/api/dokky/cloud/billing/plans").status_code == 200
    assert client.get("/api/dokky/cloud/billing/links/bl_9d1c6a").status_code == 200
    for method, path in (("post", "billing/checkout"), ("post", "billing/topup"), ("post", "billing/renewal"), ("put", "billing/links"), ("delete", "billing/links/bl_1"), ("get", "billing/links")):
        extra = {"json": {}} if method in {"post", "put"} else {}
        assert getattr(client, method)(f"/api/dokky/cloud/{path}", **extra).status_code in {404, 405}, path
    assert [call[:2] for call in sent] == [("GET", "/billing/plans"), ("GET", "/billing/links/bl_9d1c6a")]
    assert API.startswith(ORIGIN)
