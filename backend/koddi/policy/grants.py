from __future__ import annotations

import os
from urllib.parse import urlsplit

from koddi.storage.db import db, get_setting


# отметка явного выбора «Разрешить всё» без проекта: только она даёт полный доступ новым проектам
FULL_ACCOUNT_KEY = "permission_full_account:"


def current_owner() -> str:
    user = get_setting("dokky_user", {})
    return str(user.get("id") or user.get("user_id") or "__test__")


def permission_profile(project_id: int | None = None, *, owner: str | None = None) -> str:
    owner = owner if owner is not None else current_owner()
    saved = get_setting("permission_profile:" + owner)
    if saved == "read-only":
        return saved
    legacy = get_setting("permission_profile", "ask")
    if saved is None and legacy == "read-only" and (
        os.getenv("KODDI_INTERNAL_TEST") == "1"
        or get_setting("coding_preferences_legacy_owner") == owner
    ):
        return legacy
    if project_id is not None:
        with db() as conn:
            grant = conn.execute(
                "SELECT automatic,approval_profile FROM project_grants WHERE owner_id=? AND project_id=?",
                (owner, project_id),
            ).fetchone()
        if grant is not None:
            return "full" if grant[1] == "allow-all" else grant[1] or {1: "trusted", 2: "full"}.get(grant[0], "ask")
        # полный доступ аккаунта доходит до проектов без своего выбора, только если человек выбрал его сам
        # без проекта (1.0.2+). Старое значение могло остаться от выбора в другом проекте
        if saved in {"full", "allow-all"} and get_setting(FULL_ACCOUNT_KEY + owner) is not True:
            return "trusted"
    if saved is None and os.getenv("KODDI_INTERNAL_TEST") == "1":
        return "full" if legacy == "allow-all" else legacy
    value = "full" if saved == "allow-all" else saved or "trusted"
    return value if isinstance(value, str) and value in {"ask", "trusted", "full"} else "trusted"


# сайты, разрешённые человеком в этом чате: живут до перезапуска приложения
_fetch_hosts: dict[tuple[str, int], set[str]] = {}


def _fetch_host(url: str) -> str:
    try:
        return (urlsplit(url).hostname or "").casefold()
    except ValueError:
        return ""


def fetch_host_allowed(chat_id: int | None, url: str) -> bool:
    host = _fetch_host(url)
    return bool(host and chat_id is not None and host in _fetch_hosts.get((current_owner(), chat_id), ()))


def allow_fetch_host(chat_id: int | None, url: str) -> None:
    host = _fetch_host(url)
    if host and chat_id is not None:
        _fetch_hosts.setdefault((current_owner(), chat_id), set()).add(host)


def private_network_allowed(project_id: int | None) -> bool:
    if project_id is None:
        return False
    with db() as conn:
        row = conn.execute(
            "SELECT private_network FROM project_grants WHERE owner_id=? AND project_id=?",
            (current_owner(), project_id),
        ).fetchone()
    return bool(row[0]) if row else True
