from __future__ import annotations

import json
import time

import httpx
import pytest

import koddi.storage.db as storage
from koddi.dokky import coding
from koddi.dokky.client import API, DokkyClient, DokkyError
from koddi.remote.client import RemoteAIClient


class Store:
    def __init__(self):
        self.value = {'access_token': 'initial', 'refresh_token': 'refresh-initial', 'expires_at': time.time() + 900}

    def load(self):
        return self.value.copy()

    def save(self, value):
        self.value = value.copy()

    def clear(self):
        self.value = {}


@pytest.fixture
def chat(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, 'DB_PATH', tmp_path / 'rotation.sqlite3')
    storage.init_db()
    storage.set_setting('dokky_user', {'id': 'owner-a'})
    with storage.db() as conn:
        chat_id = conn.execute("INSERT INTO chats(title,mode,owner_id) VALUES ('rotation','development','owner-a')").lastrowid
    coding.save(chat_id, {'session': {'id': 'cs_' + 'a' * 24, 'reasoning_mode': 'pro', 'version': 1}, 'tools': [], 'messages': [], 'history': None})
    return chat_id


def make_client(handler, store, monkeypatch):
    transport = httpx.MockTransport(handler)
    account = DokkyClient(transport=transport, store=store)
    monkeypatch.setattr('koddi.dokky.client.dokky', account)
    return RemoteAIClient({'provider': 'dokky', 'base_url': API}, transport=transport), account


async def collect(client, chat):
    return [event async for event in client.stream(model='dokky-code', messages=[{'role': 'user', 'content': 'Continue'}], tools=[], options={'_chat_id': chat})]


def error(code):
    return httpx.Response(200, text='data: ' + json.dumps({'error': {'code': code, 'message': 'upstream failed'}}) + '\n\n')


def success():
    frames = [
        {'choices': [{'delta': {'content': 'Done'}}]},
        {'choices': [{'delta': {}, 'finish_reason': 'stop'}]},
        {'choices': [], 'usage': {'prompt_tokens': 5}, 'dokky': {'history': {'length': 2, 'hash': 'h'}}},
    ]
    return httpx.Response(200, text=''.join('data: ' + json.dumps(frame) + '\n\n' for frame in frames) + 'data: [DONE]\n\n')


@pytest.mark.asyncio
async def test_token_rotation_keeps_original_sse_error(chat, monkeypatch):
    store = Store()
    requests = []

    def handler(request):
        requests.append(request)
        store.value['access_token'] = 'rotated'
        return error('upstream_overloaded')

    client, _ = make_client(handler, store, monkeypatch)
    with pytest.raises(DokkyError) as result:
        await collect(client, chat)
    assert result.value.code == 'upstream_overloaded'
    assert len(requests) == 1
    assert store.load()['access_token'] == 'rotated'


@pytest.mark.asyncio
@pytest.mark.parametrize('rotate', [False, True])
@pytest.mark.parametrize('code', ['invalid_token', 'session_revoked', 'upstream_overloaded'])
@pytest.mark.parametrize('http_error', [False, True])
async def test_real_account_switch_still_rejects_sse_error(chat, monkeypatch, rotate, code, http_error):
    store = Store()
    requests = []

    def handler(request):
        requests.append(request)
        storage.set_setting('dokky_user', {'id': 'owner-b'})
        if rotate:
            store.value['access_token'] = 'other-account'
        return httpx.Response(401, json={'error': {'code': code}}) if http_error else error(code)

    client, _ = make_client(handler, store, monkeypatch)
    with pytest.raises(DokkyError) as result:
        await collect(client, chat)
    assert result.value.code == 'session_changed'
    assert len(requests) == 1
    assert coding.load(chat) is None


@pytest.mark.asyncio
@pytest.mark.parametrize('http_error', [False, True])
async def test_invalid_old_token_retries_with_already_refreshed_token(chat, monkeypatch, http_error):
    store = Store()
    requests = []

    def handler(request):
        assert request.url.path.endswith('/chat/completions')
        requests.append((request.headers['Authorization'], request.headers['Idempotency-Key'], request.content))
        if len(requests) == 1:
            store.value['access_token'] = 'rotated'
            return httpx.Response(401, json={'error': {'code': 'invalid_token'}}) if http_error else error('invalid_token')
        return success()

    client, account = make_client(handler, store, monkeypatch)

    async def no_refresh(*args, **kwargs):
        pytest.fail('уже обновлённый токен не должен обновляться повторно')

    monkeypatch.setattr(account, 'tokens', no_refresh)
    result = await collect(client, chat)
    assert any(event.get('content') == 'Done' for event in result)
    assert [request[0] for request in requests] == ['Bearer initial', 'Bearer rotated']
    assert requests[0][1:] == requests[1][1:]
    assert coding.pending(chat) is None


@pytest.mark.asyncio
@pytest.mark.parametrize('rotated', [False, True])
async def test_revocation_does_not_clear_a_refreshed_token(chat, monkeypatch, rotated):
    store = Store()

    def handler(request):
        if rotated:
            store.value['access_token'] = 'rotated'
        return error('session_revoked')

    client, _ = make_client(handler, store, monkeypatch)
    with pytest.raises(DokkyError) as result:
        await collect(client, chat)
    assert result.value.code == 'session_revoked'
    assert bool(store.load()) is rotated


@pytest.mark.asyncio
async def test_invalid_current_token_refreshes_with_its_original_client(chat, monkeypatch):
    from urllib.parse import parse_qs

    store = Store()
    store.value['client_id'] = 'dokky-app'
    requests = []
    refreshes = []

    def handler(request):
        if request.url.path == '/oauth/token':
            refreshes.append(parse_qs(request.content.decode()))
            return httpx.Response(200, json={'access_token': 'rotated', 'refresh_token': 'refresh-rotated', 'expires_in': 900, 'token_type': 'Bearer'})
        requests.append((request.headers['Authorization'], request.headers['Idempotency-Key'], request.content))
        return error('invalid_token') if len(requests) == 1 else success()

    client, _ = make_client(handler, store, monkeypatch)
    await collect(client, chat)
    assert refreshes == [{'client_id': ['dokky-app'], 'grant_type': ['refresh_token'], 'refresh_token': ['refresh-initial']}]
    assert [request[0] for request in requests] == ['Bearer initial', 'Bearer rotated']
    assert requests[0][1:] == requests[1][1:]


@pytest.mark.asyncio
async def test_token_retry_replaces_partial_output_without_repeating_actions(chat, monkeypatch):
    store = Store()
    requests = []

    def handler(request):
        requests.append((request.headers['Idempotency-Key'], request.content))
        if len(requests) == 1:
            store.value['access_token'] = 'rotated'
            partial = {'choices': [{'delta': {'content': 'partial'}}]}
            failure = {'error': {'code': 'invalid_token'}}
            return httpx.Response(200, text='data: ' + json.dumps(partial) + '\n\ndata: ' + json.dumps(failure) + '\n\n')
        return success()

    client, _ = make_client(handler, store, monkeypatch)
    result = await collect(client, chat)
    assert requests[0] == requests[1]
    assert result[0] == {'content': 'partial'}
    assert result[1] == {'reset': True}
    assert result[2] == {'content': 'Done'}
    assert not any('tool_call' in event for event in result)
