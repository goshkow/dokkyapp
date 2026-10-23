from __future__ import annotations

import asyncio
import base64
import ipaddress
import itertools
import json
import mimetypes
import re
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from functools import wraps
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse, urlsplit

import httpx

from koddi.policy.network import (
    DEFAULT_LIMITS,
    REDIRECT_STATUSES,
    NetworkAccessError,
    fetch_response,
    resolve_target,
    validate_url,
)
from koddi.storage.db import db, runtime_path


_SESSION_IDS = itertools.count(1)
_PLAYWRIGHT: Any | None = None
_BROWSER: Any | None = None
_PAGES: dict[int, Any] = {}
_SESSION_EVENTS: dict[int, dict[str, list[dict[str, Any]]]] = {}
_META: dict[int, dict[str, Any]] = {}
_NOTIFY: Callable[[str, dict[str, Any]], None] | None = None
_MAX_PAGES = 4
_MAX_FILE_BYTES = 20 * 1024 * 1024
_VIEWPORT = {"width": 1280, "height": 800}
# наружу из браузера ходит только петля; всё остальное режется самим Chromium даже при редиректах
_HOST_RULES = "MAP * ~NOTFOUND , EXCLUDE localhost , EXCLUDE 127.0.0.1 , EXCLUDE *.localhost"
_MAX_BROWSER_REQUESTS = 300
_MAX_BROWSER_BYTES = 20 * 1024 * 1024
_BROWSER_THREAD = ThreadPoolExecutor(max_workers=1, thread_name_prefix="koddi-browser")
_NETWORK_THREAD = ThreadPoolExecutor(max_workers=1, thread_name_prefix="koddi-browser-http")


def _network_call(function, *args, **kwargs):
    # обработчики sync Playwright уже находятся внутри его asyncio-цикла
    return _NETWORK_THREAD.submit(lambda: asyncio.run(function(*args, **kwargs))).result()


def browser_thread(function):
    """Синхронные сессии Playwright живут в потоке, который их создал."""

    @wraps(function)
    def wrapped(*args, **kwargs):
        return _BROWSER_THREAD.submit(function, *args, **kwargs).result()

    return wrapped


async def web_fetch(
    url: str, max_chars: int = 120_000, *, allow_private: bool = False,
) -> dict:
    if not isinstance(max_chars, int) or not 1 <= max_chars <= DEFAULT_LIMITS.max_bytes:
        raise NetworkAccessError("invalid character limit")
    response = await fetch_response(url, allow_private=allow_private)
    response.raise_for_status()
    bounded_text = response.text
    text = bounded_text[:max_chars]
    title = ""
    try:
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(text, "html.parser")
        title = soup.title.string.strip() if soup.title and soup.title.string else ""
        for tag in soup(["script", "style", "noscript"]):
            tag.decompose()
        summary = "\n".join(
            line.strip() for line in soup.get_text("\n").splitlines() if line.strip()
        )[:4000]
    except Exception:
        summary = text[:4000]
    return {
        "ok": True,
        "url": str(response.url),
        "title": title,
        "status_code": response.status_code,
        "content_type": response.headers.get("content-type"),
        "summary": summary,
        "content": text,
        "truncated": len(bounded_text) > max_chars,
    }


async def web_search(query: str, limit: int = 10) -> dict:
    """Поиск, при котором страница выдачи целиком в контекст модели не попадает."""
    if not isinstance(query, str) or not query.strip() or len(query) > 4096:
        raise NetworkAccessError("invalid search query")
    url = httpx.URL("https://html.duckduckgo.com/html/", params={"q": query})
    response = await fetch_response(str(url))
    response.raise_for_status()
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(response.text, "html.parser")
    results: list[dict[str, str]] = []
    for link in soup.select(".result__a"):
        href = str(link.get("href") or "")
        if href.startswith("//"):
            href = f"https:{href}"
        if urlparse(href).path.startswith("/l/"):
            href = unquote(parse_qs(urlparse(href).query).get("uddg", [""])[0])
        title = link.get_text(" ", strip=True)
        snippet_node = (
            link.find_parent(class_="result").select_one(".result__snippet")
            if link.find_parent(class_="result")
            else None
        )
        if href and title:
            results.append(
                {
                    "title": title,
                    "url": href,
                    "snippet": snippet_node.get_text(" ", strip=True) if snippet_node else "",
                }
            )
        if len(results) >= min(max(limit, 1), 25):
            break
    return {"ok": True, "query": query, "results": results, "source": "DuckDuckGo"}


def _ensure_browser() -> Any:
    global _PLAYWRIGHT, _BROWSER
    if _BROWSER is not None:
        return _BROWSER
    from playwright.sync_api import sync_playwright

    _PLAYWRIGHT = sync_playwright().start()
    try:
        _BROWSER = _PLAYWRIGHT.chromium.launch(
            headless=True,
            args=["--force-webrtc-ip-handling-policy=disable_non_proxied_udp", f"--host-resolver-rules={_HOST_RULES}"],
        )
    except BaseException:
        _PLAYWRIGHT.stop()
        _PLAYWRIGHT = None
        raise
    return _BROWSER


# описание сессий и уведомления интерфейса
def set_notifier(function: Callable[[str, dict[str, Any]], None] | None) -> None:
    """Интерфейс подписывается на открытие и закрытие страниц (вызов приходит из потока браузера)."""
    global _NOTIFY
    _NOTIFY = function


def _notify(event: str, data: dict[str, Any]) -> None:
    function = _NOTIFY
    if function is None:
        return
    try:
        function(event, data)
    except Exception:  # noqa: BLE001 - интерфейс не должен ломать работу браузера
        pass


def is_loopback_url(url: str) -> bool:
    try:
        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
    except ValueError:
        return False
    if parts.scheme not in {"http", "https", "ws", "wss"} or not host:
        return False
    if host == "localhost" or host.endswith(".localhost"):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def local_preview_allowed(project_id: int | None, workspace: Path | None) -> bool:
    """Страницы на localhost открываются без отдельного разрешения, если это сервер самого агента или проект это разрешает."""
    if workspace is None:
        return False
    from koddi.policy.grants import private_network_allowed

    if private_network_allowed(project_id):
        return True
    from koddi.policy.account import local_owner
    from koddi.tools.runner import _running_servers

    return any(item["running"] for item in _running_servers(local_owner(), Path(workspace).resolve()))


def _workspace_file(url: str, workspace: Path | None) -> Path:
    if workspace is None:
        raise NetworkAccessError("file preview requires a workspace")
    parts = urlsplit(url)
    if parts.scheme != "file" or parts.netloc not in {"", "localhost"} or parts.query:
        raise NetworkAccessError("invalid file URL")
    from urllib.request import url2pathname

    from koddi.policy.paths import WorkspacePaths

    try:
        path = WorkspacePaths(Path(workspace)).resolve(url2pathname(parts.path))
    except (ValueError, OSError) as exc:
        raise NetworkAccessError(f"file is outside the workspace: {exc}") from exc
    return path


def file_url_for(path: str, workspace: Path) -> str:
    """Относительный путь проекта -> file:// внутри рабочей папки."""
    from koddi.policy.paths import WorkspacePaths

    resolved = WorkspacePaths(Path(workspace)).resolve(path)
    return resolved.as_uri()


def _info(session_id: int) -> dict[str, Any]:
    meta = _META.get(session_id, {})
    page = _PAGES.get(session_id)
    return {
        "session_id": session_id,
        "project_id": meta.get("project_id"),
        "url": meta.get("url") or getattr(page, "url", ""),
        "title": meta.get("title", ""),
        "local": bool(meta.get("local")),
    }


def session_info(session_id: int | None) -> dict[str, Any] | None:
    """Описание живой страницы (без обращения к Playwright: можно вызывать из любого потока)."""
    if session_id is None or session_id not in _PAGES:
        return None
    return _info(session_id)


def live_sessions() -> list[dict[str, Any]]:
    return [_info(session_id) for session_id in list(_PAGES)]


def latest_session_id() -> int | None:
    return next(reversed(_PAGES), None) if _PAGES else None


def _short_error(exc: BaseException) -> str:
    text = str(exc).strip().splitlines()
    return (text[0] if text else exc.__class__.__name__)[:300]


@browser_thread
def browser_open(
    url: str,
    project_id: int | None = None,
    *,
    allow_private: bool = False,
    local_preview: bool = False,
    workspace: Path | None = None,
    replace: bool = False,
) -> dict:
    if not isinstance(allow_private, bool) or not isinstance(local_preview, bool):
        raise NetworkAccessError("network options must be explicit booleans")
    file_mode = isinstance(url, str) and url.lower().startswith("file:")
    if file_mode:
        # файл рабочей папки: сеть не нужна, всё отдаёт обработчик ниже
        target_url = _workspace_file(url, workspace).as_uri()
        target = None
    else:
        target = validate_url(url)
        target_url = str(target)
    if replace and project_id is not None:
        for old_id in [key for key, meta in _META.items() if meta.get("project_id") == project_id]:
            _close_session(old_id)
    if len(_PAGES) >= _MAX_PAGES:
        raise NetworkAccessError("browser page limit exceeded: close an old page with browser.close")
    native = local_preview and not file_mode
    if target is not None and not (native and is_loopback_url(target_url)):
        _network_call(resolve_target, target, allow_private=allow_private)
    browser = _ensure_browser()
    context = browser.new_context(service_workers="block", accept_downloads=False)
    session_id = next(_SESSION_IDS)
    events: dict[str, list[dict[str, Any]]] = {"console": [], "network": []}
    meta = {
        "project_id": project_id, "url": target_url, "title": "", "native": native, "file": file_mode,
        "local": native or file_mode, "allow_private": allow_private,
        "workspace": str(workspace) if workspace is not None else None,
        "seq": 0, "shots": 0, "by_request": {},
    }
    budget = {"requests": 0, "bytes": 0}
    navigation_deadline = time.monotonic() + DEFAULT_LIMITS.total_seconds
    state = {"opening": True}

    def serve_file(route: Any) -> None:
        path = _workspace_file(route.request.url, workspace)
        if not path.is_file() or path.stat().st_size > _MAX_FILE_BYTES:
            raise NetworkAccessError("file is not available")
        mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        if path.suffix.lower() in {".js", ".mjs"}:
            mime = "text/javascript"
        route.fulfill(status=200, headers={"content-type": mime, "access-control-allow-origin": "*"}, body=path.read_bytes())

    def route_request(route: Any) -> None:
        request = route.request
        try:
            budget["requests"] += 1
            if budget["requests"] > _MAX_BROWSER_REQUESTS:
                raise NetworkAccessError("browser request limit exceeded")
            if file_mode and request.url.lower().startswith("file:"):
                serve_file(route)
                return
            if native and is_loopback_url(request.url):
                # свой сервер на этом компьютере: обычный запрос браузера, с cookie и потоками; за пределы петли не выпускается
                route.continue_()
                return
            remaining_bytes = _MAX_BROWSER_BYTES - budget["bytes"]
            remaining_seconds = (
                navigation_deadline - time.monotonic() if state["opening"]
                else DEFAULT_LIMITS.total_seconds
            )
            if remaining_bytes <= 0 or remaining_seconds <= 0:
                raise NetworkAccessError("browser resource limit exceeded")
            previous = request.redirected_from
            redirects = 0
            while previous is not None:
                redirects += 1
                if redirects > DEFAULT_LIMITS.max_redirects:
                    raise NetworkAccessError("redirect limit exceeded")
                previous = previous.redirected_from
            response = _network_call(
                fetch_response,
                request.url, allow_private=allow_private, method=request.method,
                headers=request.all_headers(), content=request.post_data_buffer,
                follow_redirects=False, limits=replace_limits(remaining_bytes, remaining_seconds),
            )
            # Playwright не маршрутизирует продолжение HTTP-редиректа повторно
            if response.status_code in REDIRECT_STATUSES:
                raise NetworkAccessError("browser HTTP redirects are not supported")
            budget["bytes"] += len(response.content)
            if budget["bytes"] > _MAX_BROWSER_BYTES:
                raise NetworkAccessError("browser byte limit exceeded")
            response_headers = {
                name: value for name, value in response.headers.items()
                if name not in {"connection", "transfer-encoding", "content-length",
                                "content-encoding", "upgrade", "trailer", "set-cookie"}
            }
            route.fulfill(
                status=response.status_code, headers=response_headers, body=response.content,
            )
        except Exception:
            route.abort("blockedbyclient")

    def route_socket(socket: Any) -> None:
        # HMR у dev-сервера на своём localhost работает; всё остальное закрыто
        if native and is_loopback_url(socket.url):
            socket.connect_to_server()
        else:
            socket.close()

    def forget_page() -> None:
        closed = session_id in _PAGES or session_id in _META
        _PAGES.pop(session_id, None)
        _SESSION_EVENTS.pop(session_id, None)
        _META.pop(session_id, None)
        if closed:
            _notify("browser.session", {"action": "closed", "session_id": session_id, "project_id": project_id})

    def stamp() -> int:
        meta["seq"] += 1
        return meta["seq"]

    def add_console(message: Any) -> None:
        entry = {"seq": stamp(), "type": message.type, "text": message.text[:2000]}
        try:
            location = message.location
            if location and location.get("url"):
                entry["at"] = f"{location['url']}:{location.get('lineNumber', 0)}"
        except Exception:  # noqa: BLE001
            pass
        events["console"].append(entry)
        del events["console"][:-200]

    def add_page_error(error: Any) -> None:
        events["console"].append({"seq": stamp(), "type": "pageerror", "text": str(error)[:2000]})
        del events["console"][:-200]

    def add_dialog(dialog: Any) -> None:
        events["console"].append({"seq": stamp(), "type": "dialog", "text": f"{dialog.type}: {dialog.message}"[:500]})
        try:
            dialog.dismiss()
        except Exception:  # noqa: BLE001
            pass

    def add_request(request: Any) -> None:
        entry = {"seq": stamp(), "method": request.method, "url": request.url, "resource_type": request.resource_type}
        events["network"].append(entry)
        meta["by_request"][request] = entry
        if len(events["network"]) > 300:
            dropped = events["network"].pop(0)
            for key in [key for key, value in meta["by_request"].items() if value is dropped]:
                meta["by_request"].pop(key, None)

    def add_response(response: Any) -> None:
        entry = meta["by_request"].get(response.request)
        if entry is not None:
            entry["status"] = response.status

    def add_failure(request: Any) -> None:
        entry = meta["by_request"].get(request)
        if entry is not None:
            entry["failure"] = str(request.failure or "failed")[:200]

    def on_navigated(frame: Any) -> None:
        if frame == frame.page.main_frame:
            budget["requests"] = budget["bytes"] = 0

    try:
        if not native:
            # прямой HTTP Chromium отключён; ответы приходят только через pinned HTTP
            context.set_offline(True)
        context.route("**/*", route_request)
        context.route_web_socket("**/*", route_socket)
        page = context.new_page()
        context.on("page", lambda other: other.close() if other != page else None)
        page.on("close", forget_page)
        page.on("crash", lambda: context.close())
        page.on("console", add_console)
        page.on("pageerror", add_page_error)
        page.on("dialog", add_dialog)
        page.on("request", add_request)
        page.on("response", add_response)
        page.on("requestfailed", add_failure)
        page.on("framenavigated", on_navigated)
        page.set_viewport_size(dict(_VIEWPORT))
        page.goto(target_url, wait_until="domcontentloaded", timeout=30_000)
        state["opening"] = False
        title = page.title()
        meta["title"] = title
        meta["url"] = page.url
        with db() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO browser_sessions(id, project_id, url, title, status) VALUES (?, ?, ?, ?, 'open')",
                (session_id, project_id, target_url, title),
            )
        _PAGES[session_id] = page
        _SESSION_EVENTS[session_id] = events
        _META[session_id] = meta
        _notify("browser.session", {"action": "opened", **_info(session_id)})
        return {"ok": True, "session_id": session_id, "url": page.url, "title": title, "local": meta["local"]}
    except BaseException:
        forget_page()
        try:
            context.close()
        except Exception:
            pass
        raise


def replace_limits(max_bytes: int, seconds: float):
    return replace(DEFAULT_LIMITS, max_bytes=min(DEFAULT_LIMITS.max_bytes, max_bytes), total_seconds=seconds)


# действия над открытой страницей
def _resolve_id(session_id: int | None) -> int | None:
    return session_id if session_id else latest_session_id()


def _target(page: Any, payload: dict, *, required: bool = True) -> Any:
    """Элемент по ref из browser.snapshot или по CSS/text-селектору."""
    ref = payload.get("ref")
    if ref not in (None, ""):
        match = re.fullmatch(r"e?(\d{1,5})", str(ref).strip())
        if not match:
            raise ValueError("ref должен выглядеть как e12 (из browser.snapshot)")
        handle = page.evaluate_handle("(n) => (window.__koddiRefs || [])[n] || null", int(match.group(1)))
        element = handle.as_element()
        if element is None or not element.evaluate("(e) => e.isConnected"):
            raise ValueError("элемент устарел: страница изменилась, вызови browser.snapshot ещё раз")
        return element
    selector = payload.get("selector")
    if selector:
        return page.locator(str(selector)).first
    if required:
        raise ValueError("нужен ref (из browser.snapshot) или selector")
    return None


def _settle(page: Any) -> None:
    try:
        page.wait_for_load_state("domcontentloaded", timeout=3000)
    except Exception:  # noqa: BLE001 - страница могла не менять документ
        pass
    page.wait_for_timeout(250)


def _outcome(session_id: int, page: Any, since: int, **extra: Any) -> dict:
    events = _SESSION_EVENTS.get(session_id, {})
    problems = [
        f"{item['type']}: {item['text']}"[:300]
        for item in events.get("console", [])
        if item["seq"] > since and item["type"] in {"error", "pageerror", "warning"}
    ][-5:]
    try:
        title = page.title()
    except Exception:  # noqa: BLE001
        title = ""
    meta = _META.get(session_id)
    if meta is not None:
        meta["url"], meta["title"] = page.url, title
    result = {"ok": True, "session_id": session_id, "url": page.url, "title": title, **extra}
    if problems:
        result["new_console_problems"] = problems
    return result


def _check_navigation(meta: dict, url: str) -> str:
    if url.lower().startswith("file:"):
        if not meta.get("file"):
            raise NetworkAccessError("file pages can only be opened in a file preview session")
        return _workspace_file(url, Path(meta["workspace"]) if meta.get("workspace") else None).as_uri()
    target = validate_url(url)
    if is_loopback_url(str(target)):
        if not meta.get("native"):
            raise NetworkAccessError("localhost is not allowed for this page: open it with browser.open after starting a dev server")
    else:
        _network_call(resolve_target, target, allow_private=bool(meta.get("allow_private")))
    return str(target)


def _navigate(session_id: int, page: Any, payload: dict) -> dict:
    meta = _META.get(session_id, {})
    since = meta.get("seq", 0)
    action = payload.get("action") or ("goto" if payload.get("url") else "")
    if action == "back":
        page.go_back(wait_until="domcontentloaded", timeout=15_000)
    elif action == "forward":
        page.go_forward(wait_until="domcontentloaded", timeout=15_000)
    elif action == "reload":
        page.reload(wait_until="domcontentloaded", timeout=30_000)
    elif action in {"goto", ""} and payload.get("url"):
        page.goto(_check_navigation(meta, str(payload["url"])), wait_until="domcontentloaded", timeout=30_000)
    else:
        raise ValueError("нужен url или action: back, forward, reload")
    return _outcome(session_id, page, since)


def _dom_html(page: Any, payload: dict) -> dict:
    limit = max(500, min(int(payload.get("max_chars") or 20000), 60000))
    script = """(el, strip) => {
      const copy = el.cloneNode(true);
      if (strip) {
        copy.querySelectorAll('script').forEach((n) => { n.textContent = n.src ? '' : '/* ' + n.textContent.length + ' chars */'; });
        copy.querySelectorAll('style').forEach((n) => { n.textContent = '/* ' + n.textContent.length + ' chars */'; });
        copy.querySelectorAll('svg').forEach((n) => { n.innerHTML = ''; });
      }
      return copy.outerHTML;
    }"""
    element = _target(page, payload, required=False)
    if element is None:
        element = page.locator("html")
    html = element.evaluate(script, payload.get("strip", True) is not False)
    return {"ok": True, "html": html[:limit], "truncated": len(html) > limit, "total_chars": len(html)}


@browser_thread
def browser_event(session_id: int | None, action: str, payload: dict | None = None) -> dict:
    payload = payload or {}
    session_id = _resolve_id(session_id)
    page = _PAGES.get(session_id) if session_id else None
    if page is None:
        return {"ok": False, "error": "browser session not found: open a page with browser.open", "session_id": session_id}
    events = _SESSION_EVENTS.get(session_id, {})
    meta = _META.get(session_id, {})
    since = meta.get("seq", 0)
    try:
        return _perform(session_id, page, events, meta, action, payload, since)
    except NetworkAccessError as exc:
        return {"ok": False, "error": str(exc), "session_id": session_id}
    except (ValueError, Exception) as exc:  # noqa: BLE001 - ошибки Playwright понятны модели в короткой форме
        return {"ok": False, "error": _short_error(exc), "session_id": session_id}


def _perform(session_id: int, page: Any, events: dict, meta: dict, action: str, payload: dict, since: int) -> dict:
    if action == "snapshot":
        from koddi.browser.snapshot import SNAPSHOT_JS

        data = page.evaluate(SNAPSHOT_JS, {"maxChars": max(1000, min(int(payload.get("max_chars") or 20000), 40000))})
        meta["url"], meta["title"] = data["url"], data["title"]
        errors = sum(1 for item in events.get("console", []) if item["type"] in {"error", "pageerror"})
        return {"ok": True, "session_id": session_id, **{key: data[key] for key in ("url", "title", "viewport", "scroll", "refs", "truncated")},
                "snapshot": data["text"], "console_errors_total": errors}
    if action == "click":
        element = _target(page, payload)
        element.click(timeout=10_000, click_count=2 if payload.get("double") else 1, button="right" if payload.get("button") == "right" else "left")
        _settle(page)
        return _outcome(session_id, page, since)
    if action == "hover":
        _target(page, payload).hover(timeout=10_000)
        page.wait_for_timeout(250)
        return _outcome(session_id, page, since)
    if action == "type":
        text = str(payload.get("text", ""))
        element = _target(page, payload, required=False)
        if element is None:
            page.keyboard.insert_text(text)
        else:
            try:
                element.fill(text, timeout=10_000)
            except Exception:  # noqa: BLE001 - не поле ввода: печатаем в сфокусированный элемент
                element.click(timeout=10_000)
                page.keyboard.insert_text(text)
        if payload.get("submit"):
            page.keyboard.press("Enter")
        _settle(page)
        return _outcome(session_id, page, since)
    if action == "press":
        key = str(payload.get("key", ""))
        if not re.fullmatch(r"[A-Za-z0-9+_\- ]{1,40}", key):
            raise ValueError("некорректное имя клавиши (например Enter, Escape, Control+A)")
        element = _target(page, payload, required=False)
        if element is not None:
            element.focus()
        page.keyboard.press(key)
        _settle(page)
        return _outcome(session_id, page, since)
    if action == "select":
        _target(page, payload).select_option(str(payload["value"]), timeout=10_000)
        _settle(page)
        return _outcome(session_id, page, since)
    if action == "scroll":
        element = _target(page, payload, required=False)
        if element is not None:
            element.scroll_into_view_if_needed(timeout=10_000)
        else:
            amount = max(0, min(int(payload.get("amount") or 600), 20000))
            where = payload.get("to") or payload.get("direction") or "down"
            script = {
                "top": "window.scrollTo(0, 0)", "bottom": "window.scrollTo(0, document.documentElement.scrollHeight)",
                "down": f"window.scrollBy(0, {amount})", "up": f"window.scrollBy(0, -{amount})",
                "left": f"window.scrollBy(-{amount}, 0)", "right": f"window.scrollBy({amount}, 0)",
            }.get(where)
            if script is None:
                raise ValueError("direction: up, down, left, right или to: top, bottom")
            page.evaluate(script)
        page.wait_for_timeout(200)
        position = page.evaluate("({y: Math.round(scrollY), height: document.documentElement.scrollHeight, viewport: innerHeight})")
        return _outcome(session_id, page, since, scroll=position)
    if action == "navigate":
        return _navigate(session_id, page, payload)
    if action == "screenshot":
        meta["shots"] += 1
        path = runtime_path("browser", f"session-{session_id}-{meta['shots']}.png")
        element = _target(page, payload, required=False)
        if element is not None:
            element.screenshot(path=str(path), timeout=10_000)
        else:
            page.screenshot(path=str(path), full_page=bool(payload.get("full_page")))
        return {"ok": True, "session_id": session_id, "path": str(path), "url": page.url}
    if action == "dom":
        return {"session_id": session_id, "url": page.url, **_dom_html(page, payload)}
    if action == "inspect":
        from koddi.browser.snapshot import INSPECT_JS

        return {"ok": True, "session_id": session_id, **_target(page, payload).evaluate(INSPECT_JS)}
    if action == "console":
        level = str(payload.get("level") or "all")
        limit = max(1, min(int(payload.get("limit") or 50), 200))
        wanted = {"all": None, "error": {"error", "pageerror"}, "warning": {"warning"}, "log": {"log", "info", "debug"}}.get(level)
        items = [item for item in events.get("console", []) if wanted is None or item["type"] in wanted][-limit:]
        if payload.get("clear"):
            events.get("console", []).clear()
        return {"ok": True, "session_id": session_id, "messages": items, "count": len(items)}
    if action == "network":
        limit = max(1, min(int(payload.get("limit") or 50), 300))
        needle = str(payload.get("contains") or "")
        items = [
            item for item in events.get("network", [])
            if (not needle or needle in item["url"])
            and (not payload.get("failed_only") or item.get("failure") or (item.get("status") or 0) >= 400)
        ][-limit:]
        if payload.get("clear"):
            events.get("network", []).clear()
            meta["by_request"].clear()
        return {"ok": True, "session_id": session_id, "url": page.url, "requests": items, "count": len(items)}
    if action == "eval":
        value = page.evaluate(str(payload["expression"]))
        text = json.dumps(value, ensure_ascii=False, default=str)
        return _outcome(session_id, page, since, result=text[:8000], truncated=len(text) > 8000)
    if action == "wait":
        timeout = max(100, min(int(float(payload.get("timeout_seconds") or 10) * 1000), 30_000))
        if payload.get("text"):
            page.get_by_text(str(payload["text"])).first.wait_for(state="visible", timeout=timeout)
        elif payload.get("selector"):
            page.wait_for_selector(str(payload["selector"]), state=payload.get("state") or "visible", timeout=timeout)
        elif payload.get("load_state"):
            page.wait_for_load_state(str(payload["load_state"]), timeout=timeout)
        else:
            page.wait_for_timeout(min(timeout, 10_000))
        return _outcome(session_id, page, since)
    if action == "resize":
        width = max(200, min(int(payload.get("width") or _VIEWPORT["width"]), 2560))
        height = max(200, min(int(payload.get("height") or _VIEWPORT["height"]), 2560))
        page.set_viewport_size({"width": width, "height": height})
        page.wait_for_timeout(250)
        return _outcome(session_id, page, since, viewport={"width": width, "height": height})
    if action == "close":
        _close_session(session_id)
        return {"ok": True, "session_id": session_id, "closed": True}
    return {"ok": False, "error": f"unsupported browser action {action}"}


def _close_session(session_id: int) -> None:
    page = _PAGES.get(session_id)
    try:
        if page is not None:
            page.context.close()
    except Exception:  # noqa: BLE001
        pass
    project_id = _META.get(session_id, {}).get("project_id")
    existed = session_id in _PAGES or session_id in _META
    _PAGES.pop(session_id, None)
    _SESSION_EVENTS.pop(session_id, None)
    _META.pop(session_id, None)
    try:
        with db() as conn:
            conn.execute("UPDATE browser_sessions SET status='closed' WHERE id=?", (session_id,))
    except Exception:  # noqa: BLE001
        pass
    if existed:
        _notify("browser.session", {"action": "closed", "session_id": session_id, "project_id": project_id})


# живой вид и ввод пользователя
@browser_thread
def browser_frame(session_id: int, quality: int = 55) -> dict:
    page = _PAGES.get(session_id)
    if page is None:
        return {"ok": False, "error": "browser session not found"}
    try:
        data = page.screenshot(type="jpeg", quality=max(20, min(int(quality), 90)), timeout=5000)
        try:
            title = page.title()
        except Exception:  # noqa: BLE001 - идёт навигация
            title = _META.get(session_id, {}).get("title", "")
        size = page.viewport_size or dict(_VIEWPORT)
        meta = _META.get(session_id)
        if meta is not None:
            meta["url"], meta["title"] = page.url, title
        return {
            "ok": True, "session_id": session_id, "url": page.url, "title": title,
            "width": size["width"], "height": size["height"], "image": base64.b64encode(data).decode("ascii"),
        }
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": _short_error(exc)}


_INPUT_KINDS = {"click", "dblclick", "wheel", "type", "key", "navigate", "back", "forward", "reload", "resize"}


@browser_thread
def browser_input(session_id: int, payload: dict) -> dict:
    """Действие пользователя в живом окне. Координаты - доли ширины и высоты кадра, чтобы не зависеть от масштаба."""
    page = _PAGES.get(session_id)
    kind = payload.get("kind")
    if page is None:
        return {"ok": False, "error": "browser session not found"}
    if kind not in _INPUT_KINDS:
        return {"ok": False, "error": "unsupported input"}
    size = page.viewport_size or dict(_VIEWPORT)
    try:
        if kind in {"click", "dblclick", "wheel"}:
            x = min(max(float(payload.get("x", 0.5)), 0.0), 1.0) * size["width"]
            y = min(max(float(payload.get("y", 0.5)), 0.0), 1.0) * size["height"]
            if kind == "wheel":
                page.mouse.move(x, y)
                page.mouse.wheel(max(-3000, min(float(payload.get("dx", 0)), 3000)), max(-3000, min(float(payload.get("dy", 0)), 3000)))
            else:
                page.mouse.click(x, y, click_count=2 if kind == "dblclick" else 1)
        elif kind == "type":
            page.keyboard.insert_text(str(payload.get("text", ""))[:2000])
        elif kind == "key":
            key = str(payload.get("key", ""))
            if not re.fullmatch(r"[A-Za-z0-9+_\- ]{1,40}", key):
                return {"ok": False, "error": "invalid key"}
            page.keyboard.press(key)
        elif kind == "resize":
            page.set_viewport_size({
                "width": max(200, min(int(payload.get("width", size["width"])), 2560)),
                "height": max(200, min(int(payload.get("height", size["height"])), 2560)),
            })
        else:
            return {**_navigate(session_id, page, {"action": kind if kind != "navigate" else "goto", "url": payload.get("url")}), "kind": kind}
        return {"ok": True, "session_id": session_id}
    except NetworkAccessError as exc:
        return {"ok": False, "error": str(exc)}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": _short_error(exc)}


@browser_thread
def browser_close(session_id: int) -> dict:
    if session_id not in _PAGES:
        return {"ok": False, "error": "browser session not found"}
    _close_session(session_id)
    return {"ok": True, "session_id": session_id}


@browser_thread
def browser_close_all() -> None:
    global _PLAYWRIGHT, _BROWSER
    for page in list(_PAGES.values()):
        try:
            page.context.close()
        except Exception:
            pass
    _PAGES.clear()
    _SESSION_EVENTS.clear()
    _META.clear()
    try:
        if _BROWSER is not None:
            _BROWSER.close()
    finally:
        try:
            if _PLAYWRIGHT is not None:
                _PLAYWRIGHT.stop()
        finally:
            _BROWSER = None
            _PLAYWRIGHT = None
