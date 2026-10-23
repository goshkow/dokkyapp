from __future__ import annotations

import asyncio
import base64
import ctypes
import hashlib
import json
import os
import secrets
import sys
import time
from urllib.parse import urlencode, urlsplit

import httpx

if os.name == "nt":
    from ctypes import wintypes

from koddi.config import data_dir
from koddi.storage.db import get_setting, set_setting

ORIGIN = "https://dokky.goshkow.com"
API = ORIGIN + "/api/v1"
CLIENT_ID = "dokky-app"
# сессии, выданные прежним клиентом, обновляются под ним же: на сервере refresh привязан к client_id
LEGACY_CLIENT_ID = "koddi-desktop"
SCOPES = "profile chats search files inference"


def account_identity():
    user = get_setting("dokky_user", {})
    return user.get("id") or user.get("user_id")


class DokkyError(RuntimeError):
    def __init__(
        self, message: str, status: int = 502, code: str = "upstream_unavailable", *, details=None
    ):
        super().__init__(message)
        self.status, self.code = status, code
        self.details = details or {}
        self.retryable = self.details.get("retryable") is True
        self.retry_after = self.details.get("retry_after_seconds") or 0
        if code == "plan_required":
            # любой отказ по тарифу закрывает запись в режиме разработки сразу, не дожидаясь следующего опроса
            try:
                from koddi.dokky.access import mark_denied

                mark_denied(message)
            except Exception:  # noqa: BLE001
                pass


def dpapi(value: bytes, *, decrypt: bool = False) -> bytes:
    """Защита токенов: DPAPI на Windows, ключ в файле 0600 на macOS."""
    if sys.platform == "darwin":
        from koddi.dokky import keychain

        try:
            return keychain.unseal(value) if decrypt else keychain.seal(value)
        except keychain.KeychainUnavailable as exc:
            raise DokkyError(
                "Нет доступа к ключу сессии. Войдите снова", 503, "credentials_unavailable"
            ) from exc
        except keychain.KeychainError as exc:
            raise DokkyError(
                "Не удалось открыть защищённую сессию. Войдите снова", 401, "invalid_token"
            ) from exc
    if os.name != "nt":
        raise DokkyError("Защищённое хранилище этой версии требует Windows или macOS", 503)

    class Blob(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]

    buffer = ctypes.create_string_buffer(value)
    source = Blob(len(value), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    output = Blob()
    crypt = ctypes.WinDLL("crypt32", use_last_error=True)
    function = crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
    function.argtypes = [
        ctypes.POINTER(Blob),
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(Blob),
    ]
    if not function(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(output)):
        raise DokkyError(
            "Не удалось открыть защищённую сессию. Войдите снова", 401, "invalid_token"
        )
    try:
        return ctypes.string_at(output.data, output.size)
    finally:
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.LocalFree.argtypes = [ctypes.c_void_p]
        kernel.LocalFree(output.data)


class CredentialStore:
    @property
    def path(self):
        return data_dir() / ("dokky-session.dpapi" if os.name == "nt" else "dokky-session.keychain")

    def load(self) -> dict:
        if not self.path.is_file():
            return {}
        try:
            value = json.loads(dpapi(self.path.read_bytes(), decrypt=True))
            if (
                not isinstance(value, dict)
                or not all(
                    isinstance(value.get(key), str) and value[key]
                    for key in ("access_token", "refresh_token")
                )
                or not isinstance(value.get("expires_at"), (int, float))
            ):
                raise ValueError("Invalid session")
            return value
        except DokkyError as exc:
            if exc.code != "credentials_unavailable":
                self.clear()
            return {}
        except (ValueError, OSError):
            self.clear()
            return {}

    def save(self, value: dict):
        path = self.path.with_name("session-" + secrets.token_hex(8) + ".tmp")
        try:
            path.write_bytes(dpapi(json.dumps(value).encode()))
            path.replace(self.path)
        finally:
            path.unlink(missing_ok=True)

    def clear(self):
        self.path.unlink(missing_ok=True)


class _PooledClient:
    """Общий клиент с пулом соединений: работает и в `async with`, и напрямую (`send`, `build_request`).

    Закрытие (`aclose`, выход из `async with`) ничего не делает: соединения живут, пока живёт приложение.
    """

    def __init__(self, client: httpx.AsyncClient):
        self._client = client

    def __getattr__(self, name: str):
        return getattr(self._client, name)

    async def aclose(self) -> None:
        return None

    async def __aenter__(self) -> httpx.AsyncClient:
        return self._client

    async def __aexit__(self, *exc) -> bool:
        return False


class DokkyClient:
    def __init__(self, *, transport=None, store=None):
        self.transport = transport
        self.store = store or CredentialStore()
        self.pending: dict | None = None
        self.result: dict = {"status": "idle", "message": ""}
        self.lock = asyncio.Lock()
        self._pool: httpx.AsyncClient | None = None
        self._pool_loop = None

    def http(self):
        if self.transport is not None:
            return httpx.AsyncClient(
                transport=self.transport, timeout=httpx.Timeout(30, read=120), follow_redirects=False
            )
        # общий клиент с пулом соединений: новое TLS-соединение на каждый запрос делало открытие чатов медленным
        loop = asyncio.get_running_loop()
        if self._pool is None or self._pool_loop is not loop:
            self._pool = httpx.AsyncClient(
                timeout=httpx.Timeout(30, read=120),
                follow_redirects=False,
                limits=httpx.Limits(max_connections=16, max_keepalive_connections=8, keepalive_expiry=60),
            )
            self._pool_loop = loop
        return _PooledClient(self._pool)

    LOGIN_SECONDS = 600

    def _login_result(self, status: str, message: str = "") -> None:
        self.result = {"status": status, "message": message, "at": time.time()}

    async def begin(self, redirect_uri: str) -> dict:
        """Начинает попытку входа: PAR (короткая ссылка вместо длинного адреса), одна попытка - один state."""
        redirect = urlsplit(redirect_uri)
        if (
            redirect.scheme != "http"
            or redirect.hostname != "127.0.0.1"
            or not redirect.port
            or redirect.port < 1024
            or redirect.path != "/api/dokky/auth/callback"
            or redirect.query
            or redirect.fragment
            or redirect.username
        ):
            raise DokkyError("Недопустимый адрес возврата", 400, "invalid_redirect_uri")
        # новая попытка закрывает прежнюю: старый state больше не принимается
        self.pending = None
        verifier = secrets.token_urlsafe(48)
        state = secrets.token_urlsafe(32)
        challenge = (
            base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
            .decode()
            .rstrip("=")
        )
        form = {
            "client_id": CLIENT_ID,
            "response_type": "code",
            "redirect_uri": redirect_uri,
            "scope": SCOPES,
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "prompt": "select_account",
        }
        try:
            async with self.http() as client:
                response = await client.post(ORIGIN + "/oauth/par", data=form)
        except httpx.TransportError as exc:
            self._login_result("error", "Не удалось связаться с Dokky. Проверьте подключение")
            raise DokkyError("Не удалось связаться с Dokky. Проверьте подключение") from exc
        if response.status_code != 201:
            message = self._par_message(response)
            self._login_result("error", message)
            raise DokkyError(message, response.status_code, "par_rejected")
        try:
            value = response.json()
            request_uri = value["request_uri"]
            lifetime = int(value.get("expires_in", self.LOGIN_SECONDS))
            if not isinstance(request_uri, str) or not request_uri or lifetime <= 0:
                raise ValueError("request_uri")
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            self._login_result("error", "Dokky вернул некорректный ответ на вход")
            raise DokkyError("Dokky вернул некорректный ответ на вход") from exc
        lifetime = min(lifetime, self.LOGIN_SECONDS)
        now = time.time()
        self.pending = {
            "state": state,
            "verifier": verifier,
            "redirect_uri": redirect_uri,
            "created": now,
            "expires": now + lifetime,
        }
        self._login_result("pending")
        # в ссылку кладутся только идентификатор приложения и короткий request_uri
        query = urlencode({"client_id": CLIENT_ID, "request_uri": request_uri})
        return {"authorize_url": ORIGIN + "/oauth/authorize?" + query, "expires_in": lifetime}

    @staticmethod
    def _par_message(response: httpx.Response) -> str:
        try:
            body = response.json()
        except ValueError:
            body = {}
        error = body.get("error") if isinstance(body, dict) else None
        text = ""
        if isinstance(error, dict):
            text = str(error.get("message") or error.get("code") or "")
        elif isinstance(error, str):
            text = str(body.get("error_description") or error)
        if response.status_code == 429:
            return "Слишком много попыток входа. Подождите минуту и повторите"
        return (text or "Dokky не принял попытку входа")[:300]

    def login_status(self) -> dict:
        """Состояние попытки входа для окна приложения: идёт, закончена, истекла (10 минут) или отменена."""
        pending = self.pending
        if pending and time.time() > pending["expires"]:
            self.pending = None
            self._login_result("expired", "Время на вход вышло")
        result = dict(self.result)
        result["pending"] = self.pending is not None
        result.pop("at", None)
        return result

    def cancel_login(self) -> None:
        self.pending = None
        self._login_result("idle")

    def check(self, response: httpx.Response):
        if response.is_error:
            try:
                error = response.json().get("error", {})
                if not isinstance(error, dict):
                    error = {}
            except (ValueError, AttributeError):
                error = {}
            if response.status_code == 401 and error.get("code") == "session_revoked":
                try:
                    sent = response.request.headers.get("Authorization", "").removeprefix("Bearer ")
                except RuntimeError:
                    sent = ""
                if sent and self.store.load().get("access_token") != sent:
                    raise DokkyError(
                        "Вход уже изменился. Повторите действие", 409, "session_changed"
                    )
                self.store.clear()
            codes = {
                403: "insufficient_scope",
                402: "quota_exceeded",
                429: "rate_limited",
                404: "not_found",
                409: "conflict",
                422: "validation_error",
            }
            messages = {
                403: "Недостаточно прав в Dokky",
                402: "Достигнут лимит тарифа Dokky",
                429: "Слишком много запросов. Повторите позже",
                404: "Объект Dokky не найден",
                409: "Данные изменились. Обновите страницу",
                422: "Dokky отклонил параметры запроса",
            }
            raise DokkyError(
                str(
                    error.get("message")
                    or messages.get(response.status_code, "Dokky временно недоступен")
                )[:1000],
                response.status_code,
                str(error.get("code") or codes.get(response.status_code, "upstream_unavailable")),
                details=error,
            )

    async def tokens(self, form: dict, *, persist: bool = True, client_id: str = CLIENT_ID) -> dict:
        async with self.http() as client:
            response = await client.post(
                ORIGIN + "/oauth/token", data={"client_id": client_id, **form}
            )
        if response.status_code in {400, 401}:
            if persist:
                self.store.clear()
            raise DokkyError("Не удалось подтвердить вход. Войдите снова", 401, "invalid_grant")
        self.check(response)
        try:
            value = response.json()
            expires = int(value.get("expires_in", 0))
        except (ValueError, TypeError, AttributeError) as exc:
            raise DokkyError("Dokky вернул некорректную сессию") from exc
        if (
            expires <= 0
            or not isinstance(value.get("access_token"), str)
            or not value["access_token"]
            or not isinstance(value.get("refresh_token"), str)
            or not value["refresh_token"]
            or value.get("token_type", "").lower() != "bearer"
        ):
            raise DokkyError("Dokky вернул некорректную сессию")
        value["expires_at"] = time.time() + expires
        value["client_id"] = client_id
        if persist:
            self.store.save(value)
        return value

    async def callback(self, *, code: str, state: str, issuer: str, error: str | None = None):
        async with self.lock:
            pending = self.pending
            if pending and time.time() > pending["expires"]:
                self.pending = None
                self._login_result("expired", "Время на вход вышло")
                pending = None
            # слушатель принимает только свою попытку: чужое и повторное получает 404 и ничего не закрывает
            if (
                not pending
                or not secrets.compare_digest(state, pending["state"])
                or issuer != ORIGIN
            ):
                raise DokkyError("Попытка входа недействительна или закончилась", 404, "not_found")
            # одна попытка - один state: после ответа слушатель закрывается сразу
            self.pending = None
            if error:
                self._login_result("denied", "Вход отменён")
                raise DokkyError("Вход отменён пользователем", 400, "access_denied")
            if not code:
                self._login_result("error", "Отсутствует код входа")
                raise DokkyError("Отсутствует код входа", 400, "invalid_grant")
            try:
                staged = await self.tokens(
                    {
                        "grant_type": "authorization_code",
                        "code": code,
                        "redirect_uri": pending["redirect_uri"],
                        "code_verifier": pending["verifier"],
                    },
                    persist=False,
                )
            except DokkyError as exc:
                if exc.code == "invalid_grant":
                    self._login_result("invalid", "Попытка входа закончилась, войдите снова")
                    raise DokkyError("Попытка входа закончилась, войдите снова", 400, "invalid_grant") from exc
                self._login_result("error", str(exc))
                raise
            try:
                await self.bootstrap(token=staged["access_token"])
            except DokkyError as exc:
                self._login_result("error", str(exc))
                raise
            try:
                self.store.save(staged)
            except Exception as exc:
                # после ошибки записи нельзя оставлять токен и профиль разных аккаунтов
                self.store.clear()
                from koddi.dokky.monitor import stop_local_runs

                await stop_local_runs()
                for key, empty in (
                    ("dokky_user", {}),
                    ("dokky_bootstrap", {}),
                    ("dokky_models", []),
                ):
                    set_setting(key, empty)
                raise DokkyError(
                    "Не удалось сохранить вход. Войдите снова", 401, "invalid_token"
                ) from exc
        self._login_result("done")
        (data_dir() / "dokky-login.signal").write_text(str(time.time_ns()), encoding="ascii")

    async def access_token(self) -> str:
        async with self.lock:
            value = self.store.load()
            if not value:
                raise DokkyError("Войдите с помощью Dokky", 401, "invalid_token")
            if value.get("expires_at", 0) <= time.time() + 30:
                value = await self.tokens(
                    {"grant_type": "refresh_token", "refresh_token": value["refresh_token"]},
                    client_id=value.get("client_id") or LEGACY_CLIENT_ID,
                )
            return value["access_token"]

    async def request(
        self, method: str, path: str, *, json=None, params=None, key=None, files=None, data=None
    ) -> httpx.Response:
        if not path.startswith("/") or ".." in path or "?" in path:
            raise ValueError("Invalid Dokky API path")
        identity = account_identity()
        headers = {"Authorization": "Bearer " + await self.access_token(), "X-Dokky-Content": "app/1"}
        if account_identity() != identity:
            raise DokkyError("Аккаунт изменился. Повторите действие", 409, "session_changed")
        if key:
            headers["Idempotency-Key"] = key
        try:
            async with self.http() as client:
                response = await client.request(
                    method, API + path, headers=headers, json=json, params=params, files=files, data=data
                )
        except httpx.TransportError as exc:
            raise DokkyError("Не удалось связаться с Dokky. Проверьте подключение") from exc
        if account_identity() != identity:
            raise DokkyError("Аккаунт изменился. Повторите действие", 409, "session_changed")
        if (
            response.status_code == 401
            and response.json().get("error", {}).get("code") == "invalid_token"
        ):
            async with self.lock:
                if account_identity() != identity:
                    raise DokkyError(
                        "Аккаунт изменился. Повторите действие", 409, "session_changed"
                    )
                value = self.store.load()
                if not value:
                    self.check(response)
                value = await self.tokens(
                    {"grant_type": "refresh_token", "refresh_token": value["refresh_token"]},
                    client_id=value.get("client_id") or LEGACY_CLIENT_ID,
                )
            headers["Authorization"] = "Bearer " + value["access_token"]
            async with self.http() as client:
                response = await client.request(
                    method, API + path, headers=headers, json=json, params=params, files=files, data=data
                )
        if account_identity() != identity:
            raise DokkyError("Аккаунт изменился. Повторите действие", 409, "session_changed")
        self.check(response)
        from koddi.dokky.privacy import PAID_PRICE_KEYS, scrub_response

        # цена плана и сброса - то, что человек платит сам: каталог оплаты её отдаёт, расход по-прежнему вырезается
        paid = path.startswith(("/billing/", "/subscription"))
        return scrub_response(response, PAID_PRICE_KEYS if paid else frozenset())

    async def bootstrap(self, *, token: str | None = None):
        if token:
            async with self.http() as client:
                response = await client.get(
                    API + "/bootstrap", headers={"Authorization": "Bearer " + token}
                )
            self.check(response)
            value = response.json()
        else:
            value = (await self.request("GET", "/bootstrap")).json()
        if value.get("contract_version") not in {None, "1"}:
            raise DokkyError("Эта версия API Dokky не поддерживается. Обновите Докки")
        models = value.get("models", [])
        if not isinstance(models, list) or any(
            not isinstance(m, dict) or not m.get("id") for m in models
        ):
            raise DokkyError("Некорректный список моделей Dokky")
        previous = get_setting("dokky_user", {})
        if previous and str(previous.get("id") or previous.get("user_id")) != str(
            value.get("user", {}).get("id") or value.get("user", {}).get("user_id")
        ):
            from koddi.dokky.monitor import stop_local_runs

            await stop_local_runs(notify=False)
        set_setting("dokky_models", models)
        set_setting("dokky_user", value.get("user", {}))
        set_setting("dokky_bootstrap", value)
        from koddi.dokky.coding import adopt_local_data

        adopt_local_data(str(value.get("user", {}).get("id") or value.get("user", {}).get("user_id") or ""))
        return value

    async def logout(self, *, expected=None):
        async with self.lock:
            value = self.store.load() if expected is None else expected
            try:
                if value:
                    async with self.http() as client:
                        response = await client.post(
                            ORIGIN + "/oauth/revoke",
                            data={
                                "client_id": value.get("client_id") or LEGACY_CLIENT_ID,
                                "token": value.get("refresh_token", ""),
                            },
                        )
                        if response.is_error:
                            raise DokkyError(
                                "Не удалось подтвердить выход на сервере", response.status_code
                            )
            finally:
                cleared = self.store.load().get("access_token") in {None, value.get("access_token")}
                if cleared:
                    self.store.clear()
                    from koddi.dokky.coding import clear_local_account_data

                    clear_local_account_data()
                    set_setting("dokky_models", [])
                    set_setting("dokky_user", {})
                    set_setting("dokky_bootstrap", {})
            return cleared

    async def stream(self, path: str, *, after: str = ""):
        headers = {
            "Authorization": "Bearer " + await self.access_token(),
            "Accept": "text/event-stream",
            "X-Dokky-Content": "app/1",
        }
        async with self.http() as client:
            async with client.stream(
                "GET", API + path, headers=headers, params={"after": after}
            ) as response:
                self.check(response)
                async for chunk in response.aiter_bytes():
                    yield chunk


dokky = DokkyClient()
