"""HTTP окна для плагинов: каталог, установка с GitHub, включение, удаление, решение по хукам и MCP."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from koddi.plugins import catalog, store
from koddi.plugins.github import NeedsChoice, PluginSourceError
from koddi.plugins.store import PluginError
from koddi.policy.account import local_owner

router = APIRouter(prefix="/api/plugins")


class InstallIn(BaseModel):
    source: str = Field(default="", max_length=500)
    ref: str = Field(default="", max_length=200)
    path: str = Field(default="", max_length=500)
    catalog_id: str = Field(default="", max_length=80)


class EnabledIn(BaseModel):
    enabled: bool


class ExecDecisionIn(BaseModel):
    digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    allow: bool


@router.get("")
def plugins_list() -> dict:
    return {"plugins": store.list_plugins(local_owner())}


@router.get("/catalog")
def plugins_catalog() -> dict:
    installed = {(item["repo"], item["path"]): item for item in store.list_plugins(local_owner())}
    items = []
    for entry in catalog.CATALOG:
        plugin = installed.get((entry["repo"], entry["path"]))
        items.append({**entry, "installed_id": plugin["id"] if plugin else None,
                      "update": bool(plugin and plugin["commit"] != entry["commit"])})
    return {"version": catalog.VERSION, "checked_at": catalog.CHECKED_AT, "items": items}


@router.post("/install")
def plugins_install(payload: InstallIn):
    owner = local_owner()
    try:
        if payload.catalog_id:
            entry = catalog.BY_ID.get(payload.catalog_id)
            if entry is None:
                raise HTTPException(404, "Плагина нет в каталоге")
            return store.install(entry["repo"], owner, path=entry["path"], pinned=entry["commit"], catalog_id=entry["id"])
        if not payload.source.strip():
            raise HTTPException(422, "Укажите репозиторий GitHub")
        return store.install(payload.source, owner, ref=payload.ref, path=payload.path)
    except NeedsChoice as exc:
        # не ошибка: в репозитории маркетплейс, человек выбирает плагин и ставит его по пути
        return {"needs_choice": True, "message": str(exc), "choices": exc.choices, "commit": exc.commit}
    except (PluginSourceError, PluginError) as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/{plugin_id}/enabled")
def plugins_enabled(plugin_id: int, payload: EnabledIn) -> dict:
    try:
        return store.set_enabled(plugin_id, payload.enabled, local_owner())
    except PluginError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.post("/{plugin_id}/exec")
def plugins_exec(plugin_id: int, payload: ExecDecisionIn) -> dict:
    owner = local_owner()
    try:
        store.get_plugin(plugin_id, owner)
    except PluginError as exc:
        raise HTTPException(404, str(exc)) from exc
    try:
        return store.decide_exec(plugin_id, payload.digest, payload.allow, owner)
    except PluginError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.get("/{plugin_id}/file")
def plugins_file(plugin_id: int, path: str) -> dict:
    try:
        return {"path": path, "text": store.plugin_file_text(plugin_id, path, local_owner())}
    except (PluginError, PluginSourceError) as exc:
        raise HTTPException(404, str(exc)) from exc


@router.delete("/{plugin_id}")
def plugins_delete(plugin_id: int) -> dict:
    try:
        store.uninstall(plugin_id, local_owner())
    except PluginError as exc:
        raise HTTPException(404, str(exc)) from exc
    return {"ok": True}
