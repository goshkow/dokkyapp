from __future__ import annotations

import json
import re
import os

from fastapi import APIRouter, Form, HTTPException, Request, UploadFile
from fastapi.responses import RedirectResponse, Response, StreamingResponse

import asyncio

from koddi.dokky.client import API, ORIGIN, DokkyError, dokky
from koddi.dokky.privacy import scrub_event_line
from koddi.policy.network import NetworkAccessError, NetworkLimits, fetch_response
from koddi.storage.db import get_setting

router = APIRouter(prefix="/api/dokky")


def failure(exc: DokkyError):
    detail = {"code": exc.code, "message": str(exc)}
    # причина конфликта оплаты и время ожидания нужны экрану, чтобы сказать человеку, что именно случилось
    if isinstance(exc.details.get("reason"), str):
        detail["reason"] = exc.details["reason"][:64]
    if isinstance(exc.details.get("retry_after_seconds"), (int, float)):
        detail["retry_after_seconds"] = int(exc.details["retry_after_seconds"])
    return HTTPException(exc.status, detail)


@router.get("/session")
async def session():
    authenticated = bool(dokky.store.load())
    return {
        "authenticated": authenticated,
        "user": get_setting("dokky_user", {}) if authenticated else {},
        "origin": ORIGIN,
        "internal_test": os.getenv("KODDI_INTERNAL_TEST") == "1",
        "bootstrap": get_setting("dokky_bootstrap", {}) if authenticated else {},
    }


@router.post("/auth/start")
async def auth_start(request: Request):
    port = request.scope["server"][1]
    try:
        return await dokky.begin(f"http://127.0.0.1:{port}/api/dokky/auth/callback")
    except DokkyError as exc:
        raise failure(exc) from exc


@router.get("/auth/status")
async def auth_status():
    return dokky.login_status()


@router.post("/auth/cancel")
async def auth_cancel():
    dokky.cancel_login()
    return {"ok": True}


@router.get("/auth/callback")
async def callback(code: str = "", state: str = "", iss: str = "", error: str | None = None):
    try:
        await dokky.callback(code=code, state=state, issuer=iss, error=error)
    except DokkyError as exc:
        raise failure(exc) from exc
    return RedirectResponse(ORIGIN + "/desktop/complete", status_code=303)


@router.post("/logout")
async def logout():
    from koddi.dokky.monitor import stop_local_runs

    expected = dokky.store.load()
    if not await stop_local_runs(expected_session=expected):
        return {"ok": True, "session_changed": True}
    try:
        cleared = await dokky.logout(expected=expected)
    except Exception:
        changed = dokky.store.load().get("access_token") not in {None, expected.get("access_token")}
        return {"ok": True, "server_revocation_confirmed": False, "session_changed": changed}
    return {"ok": True, "server_revocation_confirmed": True, "session_changed": not cleared}


@router.post("/bootstrap")
async def bootstrap():
    try:
        return await dokky.bootstrap()
    except DokkyError as exc:
        raise failure(exc) from exc


# режим чата показывает страницу сайта во фрейме; путь и формат токена задаёт сайт, приложение их только проверяет
SURFACE_PATH = "/app-surface"


def own_remote_host() -> str:
    """Id этого компьютера в удалённом доступе: его чаты сайт показывает как удалённые, у нас они и так свои."""
    from koddi.remote_control import remote_control

    value = remote_control.config().get("host_id")
    return value if isinstance(value, str) else ""


@router.get("/surface")
async def surface():
    """Есть ли на сервере страница для режима чата. Без неё приложение остаётся на своём чате."""
    if not dokky.store.load():
        return {"available": False}
    try:
        value = (await dokky.request("GET", "/app/surface")).json()
    except DokkyError as exc:
        if exc.status in {404, 405}:
            return {"available": False}
        raise failure(exc) from exc
    if value.get("protocol") != 1 or value.get("path") != SURFACE_PATH:
        return {"available": False}
    return {"available": True, "url": ORIGIN + SURFACE_PATH, "origin": ORIGIN,
            "release": str(value.get("release") or "")[:128], "host_id": own_remote_host()}


@router.post("/surface/session")
async def surface_session():
    """Короткий токен страницы сайта. Bearer приложения остаётся здесь, во фрейм уходит только этот токен."""
    try:
        value = (await dokky.request("POST", "/app/surface/session")).json()
    except DokkyError as exc:
        raise failure(exc) from exc
    token = value.get("token")
    if not isinstance(token, str) or not token.startswith("dks1.") or len(token) > 2048:
        raise HTTPException(502, {"code": "bad_surface_token", "message": "Сервер вернул непонятный ответ"})
    expires = value.get("expires_in")
    expires = expires if isinstance(expires, int) and 60 <= expires <= 3600 else 900
    return {"token": token, "expires_in": expires}


@router.post("/cloud/files")
async def upload(file: UploadFile, request: Request):
    content = await file.read(25 * 1024 * 1024 + 1)
    if len(content) > 25 * 1024 * 1024:
        raise HTTPException(413, "Вложение превышает 25 МБ")
    try:
        response = await dokky.request(
            "POST",
            "/files",
            files={"file": (file.filename or "file", content, file.content_type)},
            key=request.headers.get("Idempotency-Key"),
        )
        return response.json()
    except DokkyError as exc:
        raise failure(exc) from exc


@router.post("/cloud/speech/transcriptions")
async def transcribe(audio: UploadFile, request: Request, seconds: int = Form(0)):
    """Один сегмент записи: приложение режет речь по паузам и шлёт куски, сервер Докки распознаёт каждый."""
    content = await audio.read(25 * 1024 * 1024 + 1)
    if len(content) > 25 * 1024 * 1024:
        raise HTTPException(413, "Запись превышает 25 МБ")
    try:
        response = await dokky.request(
            "POST",
            "/speech/transcriptions",
            files={"audio": (audio.filename or "voice.webm", content, audio.content_type)},
            data={"seconds": str(max(0, min(int(seconds), 600)))},
        )
        return response.json()
    except DokkyError as exc:
        raise failure(exc) from exc


@router.get("/cloud/runs/{run_id}/events")
async def events(run_id: str, after: int = 0):
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", run_id):
        raise HTTPException(422, "Некорректный идентификатор задачи")
    client = dokky.http()
    try:
        token = await dokky.access_token()
        response = await client.send(
            client.build_request(
                "GET",
                API + f"/runs/{run_id}/events",
                params={"after": after},
                headers={"Authorization": "Bearer " + token, "Accept": "text/event-stream", "X-Dokky-Content": "app/1"},
            ),
            stream=True,
        )
        if response.is_error:
            await response.aread()
        dokky.check(response)
    except DokkyError as exc:
        await client.aclose()
        raise failure(exc) from exc
    except Exception:
        await client.aclose()
        raise HTTPException(502, "Не удалось подключиться к потоку Dokky")

    async def relay():
        revoked = False
        try:
            async for line in response.aiter_lines():
                if dokky.store.load().get("access_token") != token:
                    return
                if line == "event: auth.revoked":
                    dokky.store.clear()
                    from koddi.dokky.monitor import stop_local_runs

                    await stop_local_runs(only_if_signed_out=True)
                    revoked = True
                yield scrub_event_line(line) + "\n"
                if revoked and not line:
                    return
        finally:
            await response.aclose()
            await client.aclose()

    return StreamingResponse(
        relay(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


@router.get("/cloud/files/{file_id}/content")
async def file_content(file_id: str, request: Request):
    if not re.fullmatch(r"file_[A-Za-z0-9_-]{1,128}", file_id):
        raise HTTPException(404, "Файл не найден")
    client = dokky.http()
    response = None
    try:
        headers = {"Authorization": "Bearer " + await dokky.access_token()}
        for key in ("Range", "If-Range", "If-None-Match"):
            if key in request.headers:
                headers[key] = request.headers[key]
        response = await client.send(
            client.build_request("GET", API + f"/files/{file_id}/content", headers=headers),
            stream=True,
        )
        if response.is_error:
            await response.aread()
        dokky.check(response)
        if response.is_redirect:
            raise DokkyError("Докки вернул неожиданный адрес файла")
    except Exception as exc:
        if response is not None:
            await response.aclose()
        await client.aclose()
        if isinstance(exc, DokkyError):
            raise failure(exc) from exc
        raise HTTPException(502, "Не удалось открыть файл Докки") from exc

    async def relay_file():
        try:
            async for chunk in response.aiter_raw():
                yield chunk
        finally:
            await response.aclose()
            await client.aclose()

    forwarded = {
        key: response.headers[key]
        for key in (
            "Content-Type",
            "Content-Disposition",
            "Content-Length",
            "Content-Range",
            "Accept-Ranges",
            "Content-Encoding",
            "ETag",
            "Last-Modified",
        )
        if key in response.headers
    }
    return StreamingResponse(
        relay_file(),
        status_code=response.status_code,
        headers={
            **forwarded,
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.api_route("/cloud/{path:path}", methods=["GET", "POST", "PATCH", "PUT", "DELETE"])
async def cloud(path: str, request: Request):
    allowed = {
        "GET": [
            r"chats",
            r"chats/[\w-]+",
            r"chats/[\w-]+/(messages|branches|export)",
            r"runs/[\w-]+",
            r"files/[\w-]+(/content)?",
            r"me",
            r"me/avatar",
            r"preferences",
            r"subscription",
            r"library",
            r"artifacts/[\w-]+",
            r"billing/plans",
            r"billing/links/[\w-]+",
        ],
        "POST": [
            r"billing/links",
            r"chats",
            r"chats/[\w-]+/(messages|read)",
            r"chats/[\w-]+/messages/[\w-]+/regenerate",
            r"runs/[\w-]+/cancel",
        ],
        "PATCH": [r"chats/[\w-]+", r"chats/[\w-]+/messages/[\w-]+", r"preferences"],
        "PUT": [r"library/[\w-]+/favorite"],
        "DELETE": [r"chats/[\w-]+", r"files/[\w-]+"],
    }
    if not any(re.fullmatch(pattern, path, flags=re.ASCII) for pattern in allowed[request.method]):
        raise HTTPException(404, "Операция Dokky не поддерживается")
    body = None
    if request.method in {"POST", "PATCH", "PUT"}:
        raw = await request.body()
        if len(raw) > 2_000_000:
            raise HTTPException(413, "Слишком большой запрос")
        try:
            body = json.loads(raw) if raw else {}
        except ValueError as exc:
            raise HTTPException(422, "Некорректный JSON") from exc
        if not isinstance(body, dict):
            raise HTTPException(422, "Ожидался JSON-объект")
        if path == "billing/links":
            body = payment_link_request(body)
    try:
        response = await dokky.request(
            request.method,
            "/" + path,
            json=body,
            params=dict(request.query_params),
            key=request.headers.get("Idempotency-Key"),
        )
        if path == "billing/links":
            payment_link_response(response)
        return Response(
            response.content,
            status_code=response.status_code,
            media_type=response.headers.get("Content-Type", "application/json"),
            headers={
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
                **{
                    key: response.headers[key]
                    for key in ("Content-Disposition",)
                    if key in response.headers
                },
            },
        )
    except DokkyError as exc:
        raise failure(exc) from exc


def payment_link_request(body: dict) -> dict:
    """Ссылка на оплату: только `intent`, `plan`, `percent` и `return_app`, и только допустимые значения.

    Деньги приложение не принимает: сервер выдаёт персональную ссылку на страницу оплаты сайта, цену считает он.
    """
    allowed = {"intent", "plan", "percent", "return_app"}
    if set(body) - allowed:
        raise HTTPException(422, "Лишние поля в запросе оплаты")
    intent = body.get("intent")
    if intent not in {"plan", "topup"}:
        raise HTTPException(422, "Неизвестный вид оплаты")
    clean: dict = {"intent": intent}
    if intent == "plan":
        plan = body.get("plan")
        if not isinstance(plan, str) or not re.fullmatch(r"[a-z]{2,16}", plan):
            raise HTTPException(422, "Не указан план")
        clean["plan"] = plan
    elif "plan" in body:
        raise HTTPException(422, "План указывается только при покупке плана")
    if body.get("percent") is not None:
        if body["percent"] not in {25, 50, 100} or isinstance(body["percent"], bool):
            raise HTTPException(422, "Недопустимый процент пополнения")
        clean["percent"] = body["percent"]
    if body.get("return_app") is not None:
        if body["return_app"] not in {"dokky", "koddi"}:
            raise HTTPException(422, "Недопустимое приложение для возврата")
        clean["return_app"] = body["return_app"]
    return clean


def payment_link_response(response) -> None:
    """Ссылка из ответа открывается в браузере, поэтому она обязана вести на страницу оплаты самого сайта Докки."""
    try:
        url = str(response.json().get("url") or "")
    except (ValueError, AttributeError):
        url = ""
    if url and not url.startswith(ORIGIN + "/pay/"):
        raise HTTPException(502, {"code": "bad_payment_link", "message": "Сервер вернул непонятную ссылку на оплату"})


@router.get("/access")
async def developer_access(refresh: bool = False):
    """Доступ к Докки Code для экрана и для проверки перед записью: причина берётся из подписки на сервере."""
    from koddi.dokky.access import developer_access as read

    return await read(refresh=refresh)


_TILE_LIMITS = NetworkLimits(max_bytes=512 * 1024, total_seconds=10, read_seconds=6)
_TILE_SLOTS = asyncio.Semaphore(8)


@router.get("/tile/{z}/{x}/{y}.png")
async def map_tile(z: int, x: int, y: int):
    """Плитки карты для блока «Места»: страница приложения не ходит на чужие сайты сама, политика безопасности остаётся строгой."""
    if not 0 <= z <= 19 or not (0 <= x < 2**z and 0 <= y < 2**z):
        raise HTTPException(422, {"code": "bad_tile", "message": "Недопустимая плитка карты"})
    async with _TILE_SLOTS:
        try:
            response = await fetch_response(f"https://tile.openstreetmap.org/{z}/{x}/{y}.png", allow_private=False, limits=_TILE_LIMITS)
        except Exception as exc:
            raise HTTPException(502, {"code": "tile_unavailable", "message": "Карта недоступна"}) from exc
    if response.status_code != 200 or response.headers.get("content-type", "").split(";")[0].strip().lower() != "image/png":
        raise HTTPException(502, {"code": "tile_unavailable", "message": "Карта недоступна"})
    return Response(response.content, media_type="image/png", headers={"Cache-Control": "private, max-age=86400", "X-Content-Type-Options": "nosniff"})


_IMAGE_LIMITS = NetworkLimits(max_bytes=6 * 1024 * 1024, total_seconds=15, read_seconds=8)
_IMAGE_SLOTS = asyncio.Semaphore(6)
_IMAGE_TYPES = {"image/png", "image/jpeg", "image/gif", "image/webp", "image/avif", "image/bmp"}


@router.get("/media")
async def external_image(url: str):
    """Картинки из ответов Докки открываются через защищённую загрузку, а не напрямую из окна.

    Адрес проверяется как для инструментов агента: только публичные HTTP(S), закреплённый DNS,
    лимиты размера и времени. SVG и не-изображения не отдаются.
    """
    async with _IMAGE_SLOTS:
        try:
            response = await fetch_response(url, allow_private=False, limits=_IMAGE_LIMITS)
        except NetworkAccessError as exc:
            raise HTTPException(422, {"code": "bad_media_url", "message": "Адрес изображения недоступен"}) from exc
        except Exception as exc:
            raise HTTPException(502, {"code": "media_unavailable", "message": "Не удалось загрузить изображение"}) from exc
    kind = response.headers.get("content-type", "").split(";")[0].strip().lower()
    if response.status_code != 200 or kind not in _IMAGE_TYPES:
        raise HTTPException(415, {"code": "not_an_image", "message": "Это не изображение"})
    return Response(
        response.content,
        media_type=kind,
        headers={
            "Cache-Control": "private, max-age=3600",
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'; sandbox",
            "Cross-Origin-Resource-Policy": "same-origin",
        },
    )
