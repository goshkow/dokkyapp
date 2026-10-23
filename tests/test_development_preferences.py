from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import koddi.storage.db as storage
from koddi.api import create_app
from koddi.dokky import activity, coding
from koddi.policy import preferences
from koddi.policy.engine import decide
from koddi.policy.grants import permission_profile, private_network_allowed


@pytest.fixture
def profile(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DB_PATH", tmp_path / "preferences.sqlite3")
    storage.init_db()
    storage.set_setting("dokky_user", {"id": "owner-a"})
    monkeypatch.delenv("KODDI_INTERNAL_TEST")
    from koddi.dokky.client import dokky

    monkeypatch.setattr(dokky.store, "load", lambda: {"access_token": "test-only"})
    with storage.db() as conn:
        project = conn.execute("INSERT INTO projects(name,path) VALUES ('a',?)", (str(tmp_path),)).lastrowid
        other = conn.execute("INSERT INTO projects(name,path) VALUES ('b',?)", (str(tmp_path / 'b'),)).lastrowid
        chat = conn.execute("INSERT INTO chats(title,project_id,owner_id) VALUES ('chat',?,'owner-a')", (project,)).lastrowid
    activity._active.clear()
    return TestClient(create_app()), project, other, chat, tmp_path


def test_defaults_and_explicit_account_preferences_are_independent(profile):
    client, project, other, _, _ = profile
    assert client.get('/api/settings').json()['coding_reasoning_mode'] == 'pro'
    assert permission_profile(project) == 'trusted'
    assert private_network_allowed(project)
    response = client.put('/api/settings', json={'project_id': project, 'permission_profile': 'full', 'confirm_full_access': True, 'coding_reasoning_mode': 'max', 'private_network': False, 'coding_keepalive': False})
    assert response.status_code == 200
    assert preferences.reasoning_mode() == 'max'
    assert permission_profile(other) == 'full'
    assert private_network_allowed(other)
    # прежний ручной выбор проекта не заменяется последним режимом другого проекта
    assert client.put('/api/settings', json={'project_id': other, 'permission_profile': 'ask'}).status_code == 200
    assert permission_profile(project) == 'full'
    assert permission_profile(other) == 'ask'
    assert permission_profile(None) == 'ask'
    storage.set_setting('dokky_user', {'id': 'owner-b'})
    assert preferences.reasoning_mode() == 'pro'
    assert permission_profile(project) == 'trusted'
    assert private_network_allowed(project)
    assert client.get('/api/settings').json()['coding_keepalive'] is True
    storage.set_setting('dokky_user', {'id': 'owner-a'})
    assert preferences.reasoning_mode() == 'max'
    assert not private_network_allowed(project)
    assert client.get('/api/settings').json()['coding_keepalive'] is False


def test_invalid_settings_do_not_mutate_last_choice(profile):
    client, _, _, _, _ = profile
    assert client.put('/api/settings', json={'coding_reasoning_mode': 'max', 'permission_profile': 'full', 'confirm_full_access': True, 'text_size': -1}).status_code == 422
    assert preferences.reasoning_mode() == 'pro'
    assert permission_profile(None) == 'trusted'
    storage.set_setting('coding_reasoning_mode:owner-a', ['invalid'])
    assert preferences.reasoning_mode() == 'pro'


def test_legacy_grants_keep_manual_mode_and_network_denial(profile):
    client, project, _, _, _ = profile
    with storage.db() as conn:
        conn.execute('INSERT INTO project_grants(owner_id,project_id,automatic,private_network) VALUES (?, ?, 0, 0)', ('owner-a', project))
    assert permission_profile(project) == 'ask'
    assert not private_network_allowed(project)
    assert client.put('/api/settings', json={'coding_keepalive': True}).status_code == 200
    assert permission_profile(project) == 'ask'
    assert not private_network_allowed(project)


def test_full_access_keeps_path_guards_and_computer_consent(profile):
    client, project, _, _, root = profile
    assert client.put('/api/settings', json={'project_id': project, 'permission_profile': 'full', 'confirm_full_access': True}).status_code == 200
    assert decide('shell.run', {'command': 'rm file.txt'}, root, project).action == 'allow'
    assert decide('file.create', {'path': '../private.txt'}, root, project).action == 'block'
    assert decide('file.create', {'path': 'file.txt'}, None, project).action == 'block'
    assert decide('unknown.tool', {}, root, project).action == 'block'
    assert decide('computer.use', {'action': 'observe', 'pid': 1}, root, project).action == 'allow'
    assert client.put('/api/computer/consent', json={'enabled': False}).status_code == 200
    assert decide('computer.use', {'action': 'observe', 'pid': 1}, root, project).action == 'block'


def test_activity_lease_is_chat_and_account_scoped_and_expires(profile, monkeypatch):
    client, _, _, chat, _ = profile
    clock = [1000.0]
    monkeypatch.setattr(activity.time, 'monotonic', lambda: clock[0])
    assert not activity.recent('owner-a', chat)
    assert client.post(f'/api/chats/{chat}/coding-session/activity').status_code == 200
    assert activity.recent('owner-a', chat)
    assert not activity.recent('owner-a', chat + 1)
    assert not activity.recent('owner-b', chat)
    clock[0] += activity.IDLE_SECONDS + 1
    assert not activity.recent('owner-a', chat)
    storage.set_setting('dokky_user', {'id': 'owner-b'})
    assert client.post(f'/api/chats/{chat}/coding-session/activity').status_code == 404
    assert not activity.recent('owner-b', chat)


def test_successful_session_choice_is_next_chat_default(profile, monkeypatch):
    client, _, _, chat, _ = profile

    async def switch(chat_id, mode, acknowledged):
        assert chat_id == chat and mode == 'light'
        return {'id': 'cs_' + 'a' * 24, 'reasoning_mode': mode, 'version': 1}

    monkeypatch.setattr(coding, 'switch_mode', switch)
    assert client.patch(f'/api/chats/{chat}/coding-session', json={'reasoning_mode': 'light'}).status_code == 200
    assert preferences.reasoning_mode() == 'light'
    assert client.get('/api/settings').json()['coding_reasoning_mode'] == 'light'


def test_legacy_reasoning_choice_migrates_only_for_its_owner(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, 'DB_PATH', tmp_path / 'legacy.sqlite3')
    storage.init_db()
    storage.set_setting('dokky_user', {'id': 'original'})
    storage.set_setting('coding_reasoning_mode', 'light')
    with storage.db() as conn:
        conn.execute("DELETE FROM schema_migrations WHERE version='0018_approval_preferences.sql'")
        # проверяем миграцию старого профиля до добавления нового столбца
        conn.execute('ALTER TABLE project_grants DROP COLUMN approval_profile')
    storage.init_db()
    monkeypatch.delenv('KODDI_INTERNAL_TEST')
    assert preferences.reasoning_mode() == 'light'
    storage.set_setting('dokky_user', {'id': 'new-owner'})
    assert preferences.reasoning_mode() == 'pro'


def test_new_account_choice_overrides_legacy_read_only(profile):
    client, project, _, _, _ = profile
    storage.set_setting('coding_preferences_legacy_owner', 'owner-a')
    storage.set_setting('permission_profile', 'read-only')
    assert permission_profile(project) == 'read-only'
    assert client.put('/api/settings', json={'project_id': project, 'permission_profile': 'full', 'confirm_full_access': True}).status_code == 200
    assert permission_profile(project) == 'full'
    assert permission_profile(None) == 'full'


def test_setting_updates_keep_the_captured_owner_on_account_switch(profile, monkeypatch):
    client, _, _, _, _ = profile
    import importlib

    api_module = importlib.import_module("koddi.api.app")

    original = api_module.set_setting

    def switch_after_profile(key, value):
        original(key, value)
        if key == 'permission_profile:owner-a':
            storage.set_setting('dokky_user', {'id': 'owner-b'})

    monkeypatch.setattr(api_module, 'set_setting', switch_after_profile)
    response = client.put('/api/settings', json={'permission_profile': 'full', 'confirm_full_access': True, 'coding_reasoning_mode': 'max'})
    assert response.status_code == 409
    assert storage.get_setting('coding_reasoning_mode:owner-a') == 'max'
    assert storage.get_setting('coding_reasoning_mode:owner-b') is None
    assert storage.get_setting('permission_profile:owner-b') is None


def test_unstructured_profile_is_rejected_before_any_write(profile):
    client, project, _, _, _ = profile
    assert client.put('/api/settings', json={'project_id': project, 'permission_profile': ['full'], 'coding_reasoning_mode': 'max'}).status_code == 422
    assert preferences.reasoning_mode() == 'pro'


def test_legacy_full_alias_keeps_confirmation_and_storage_compatibility(profile):
    client, project, _, _, _ = profile
    assert client.put('/api/settings', json={'project_id': project, 'permission_profile': 'allow-all'}).status_code == 422
    saved = client.put('/api/settings', json={'project_id': project, 'permission_profile': 'allow-all', 'confirm_full_access': True})
    assert saved.status_code == 200
    assert saved.json()['permission_profile'] == 'full'
    with storage.db() as conn:
        row = conn.execute('SELECT automatic,approval_profile FROM project_grants WHERE owner_id=? AND project_id=?', ('owner-a', project)).fetchone()
    assert tuple(row) == (2, 'allow-all')
    assert permission_profile(project) == 'full'
