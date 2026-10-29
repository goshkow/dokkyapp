"""Скачивание плагина с GitHub, закреплённого на коммит.

Ходим только по https на github.com и его служебные хосты (API и codeload), без токена и без чужих редиректов.
Цепочка доверия: коммит (40 hex) -> дерево коммита из API -> поддерево плагина, его хэш пересчитывается из списка
файлов -> каждый файл архива сверяется с git-хэшем blob из этого списка. Файл, которого нет в дереве или с другим
содержимым, ломает установку целиком.
"""

from __future__ import annotations

import base64
import hashlib
import io
import os
import re
import tarfile
import time
import zlib
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from urllib.parse import quote, urljoin, urlsplit

import httpx

ALLOWED_HOSTS = {"github.com", "api.github.com", "codeload.github.com", "raw.githubusercontent.com"}
API = "https://api.github.com"
CODELOAD = "https://codeload.github.com"
RAW = "https://raw.githubusercontent.com"
OWNER_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$")
REPO_RE = re.compile(r"^[A-Za-z0-9._-]{1,100}$")
REF_RE = re.compile(r"^[A-Za-z0-9._/-]{1,200}$")
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
PART_RE = re.compile(r"^[^\x00-\x1f\\/:*?\"<>|]{1,200}$")

MAX_ARCHIVE_BYTES = 120 * 1024 * 1024
MAX_FILES = 2000
MAX_TOTAL_BYTES = 40 * 1024 * 1024
MAX_FILE_BYTES = 5 * 1024 * 1024
MAX_DEPTH = 24
MAX_REDIRECTS = 3
# распакованный объём всего архива, включая папки вне плагина: защита от gzip-бомбы
MAX_UNPACKED_BYTES = 1024 * 1024 * 1024
MAX_MEMBERS = 100_000
# общий срок установки: медленная отдача по байту не держит поток бесконечно
DEADLINE_SECONDS = 300
# плагин из папки большого репозитория дешевле скачать по файлам, чем архивом всего репозитория
RAW_MAX_FILES = 300
RAW_WORKERS = 8
TIMEOUT = httpx.Timeout(30.0, connect=10.0)
USER_AGENT = "Dokky-Code-Plugins"


class PluginSourceError(ValueError):
    pass


@dataclass(frozen=True)
class Source:
    owner: str
    repo: str
    ref: str = ""
    path: str = ""

    @property
    def full_name(self) -> str:
        return f"{self.owner}/{self.repo}"


@dataclass
class Snapshot:
    """Проверенное содержимое плагина: файлы по пути от корня плагина."""

    source: Source
    commit: str
    tree: str
    default_branch: str
    license: str
    files: dict[str, bytes] = field(default_factory=dict)
    skipped_links: list[str] = field(default_factory=list)


def clean_path(value: str) -> str:
    """Относительный путь внутри репозитория: без `..`, абсолютных путей, обратных слешей и управляющих знаков."""
    value = (value or "").strip().strip("/")
    if not value:
        return ""
    parts = value.split("/")
    if len(parts) > MAX_DEPTH or any(part in {"", ".", ".."} or not PART_RE.match(part) for part in parts):
        raise PluginSourceError("Недопустимый путь внутри репозитория")
    return "/".join(parts)


def parse_source(text: str, *, ref: str = "", path: str = "") -> Source:
    """`owner/repo`, `https://github.com/owner/repo(.git)` или ссылка `.../tree/<ref>/<path>`."""
    text = (text or "").strip()
    if not text or len(text) > 500:
        raise PluginSourceError("Укажите репозиторий GitHub: owner/repo или ссылку https://github.com/owner/repo")
    if "://" in text:
        parts = urlsplit(text)
        if parts.scheme != "https" or (parts.hostname or "").lower() not in {"github.com", "www.github.com"}:
            raise PluginSourceError("Плагины скачиваются только по https с github.com")
        if parts.username or parts.password or parts.port or parts.query or parts.fragment:
            raise PluginSourceError("Ссылка на GitHub без логина, порта и параметров")
        segments = [item for item in parts.path.split("/") if item]
    else:
        if text.startswith("/") or "\\" in text or ":" in text:
            raise PluginSourceError("Укажите репозиторий GitHub: owner/repo")
        segments = [item for item in text.split("/") if item]
    if len(segments) < 2:
        raise PluginSourceError("Укажите репозиторий GitHub: owner/repo")
    owner, repo = segments[0], segments[1].removesuffix(".git")
    rest = segments[2:]
    if rest:
        if rest[0] != "tree" or len(rest) < 2:
            raise PluginSourceError("Ссылка должна вести на репозиторий или папку (…/tree/<ветка>/<путь>)")
        ref = ref or rest[1]
        path = path or "/".join(rest[2:])
    if not OWNER_RE.match(owner) or not REPO_RE.match(repo) or repo in {".", ".."}:
        raise PluginSourceError("Недопустимое имя репозитория GitHub")
    ref = (ref or "").strip()
    if ref and (not REF_RE.match(ref) or ".." in ref or ref.startswith("/") or ref.endswith("/")):
        raise PluginSourceError("Недопустимая ветка, тег или коммит")
    return Source(owner, repo, ref, clean_path(path))


def blob_sha(data: bytes) -> str:
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def tree_sha(entries: list[dict]) -> str:
    """git-хэш дерева, собранный из рекурсивного списка: так список файлов привязан к дереву коммита."""
    children: dict[str, list[dict]] = {}
    for entry in entries:
        parent, _, name = entry["path"].rpartition("/")
        children.setdefault(parent, []).append({**entry, "name": name})

    def build(prefix: str) -> str:
        items = []
        for entry in children.get(prefix, []):
            mode = entry["mode"]
            if entry["type"] == "tree":
                digest = build(entry["path"])
                mode = "40000"
            else:
                digest = entry["sha"]
            # git сортирует папки так, будто у имени есть завершающий слеш
            key = entry["name"].encode() + (b"/" if entry["type"] == "tree" else b"")
            items.append((key, mode.encode() + b" " + entry["name"].encode() + b"\0" + bytes.fromhex(digest)))
        body = b"".join(raw for _, raw in sorted(items))
        return hashlib.sha1(b"tree %d\0" % len(body) + body).hexdigest()

    return build("")


class GitHub:
    """Анонимный клиент: без токенов, только разрешённые хосты, редиректы проверяются вручную."""

    def __init__(self, transport: httpx.BaseTransport | None = None):
        self.client = httpx.Client(
            timeout=TIMEOUT, follow_redirects=False, transport=transport,
            headers={"User-Agent": USER_AGENT, "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"},
        )
        self.deadline = time.monotonic() + DEADLINE_SECONDS

    def _in_time(self) -> None:
        if time.monotonic() > self.deadline:
            raise PluginSourceError("GitHub отвечает слишком долго, установка прервана")

    def close(self) -> None:
        self.client.close()

    def __enter__(self) -> GitHub:
        return self

    def __exit__(self, *_) -> None:
        self.close()

    @staticmethod
    def _check_url(url: str) -> None:
        parts = urlsplit(url)
        if parts.scheme != "https" or (parts.hostname or "").lower() not in ALLOWED_HOSTS or parts.username or parts.port:
            raise PluginSourceError("Плагины скачиваются только по https с github.com")

    def _send(self, url: str, *, stream: bool = False) -> httpx.Response:
        for _ in range(MAX_REDIRECTS + 1):
            self._check_url(url)
            self._in_time()
            try:
                response = self.client.send(self.client.build_request("GET", url), stream=stream)
            except httpx.HTTPError as exc:
                raise PluginSourceError(f"GitHub недоступен: {type(exc).__name__}") from exc
            if response.status_code in {301, 302, 303, 307, 308}:
                location = response.headers.get("location", "")
                response.close()
                url = urljoin(url, location)
                continue
            if response.status_code in {403, 429} and response.headers.get("x-ratelimit-remaining") == "0":
                response.close()
                raise PluginSourceError("GitHub временно ограничил число запросов без входа. Попробуйте через час")
            if response.status_code == 404:
                response.close()
                raise PluginSourceError("Репозиторий, ветка или папка не найдены на GitHub (или репозиторий закрыт)")
            if response.status_code >= 400:
                response.close()
                raise PluginSourceError(f"GitHub ответил ошибкой {response.status_code}")
            return response
        raise PluginSourceError("Слишком много перенаправлений GitHub")

    def json(self, path: str) -> dict:
        response = self._send(API + path)
        if len(response.content) > 8 * 1024 * 1024:
            raise PluginSourceError("Слишком большой ответ GitHub")
        try:
            data = response.json()
        except ValueError as exc:
            raise PluginSourceError("GitHub вернул не JSON") from exc
        if not isinstance(data, dict):
            raise PluginSourceError("Неожиданный ответ GitHub")
        return data

    def repo_info(self, source: Source) -> dict:
        data = self.json(f"/repos/{source.owner}/{source.repo}")
        if data.get("private") or data.get("visibility", "public") != "public":
            raise PluginSourceError("Ставятся только открытые репозитории")
        license_info = data.get("license") if isinstance(data.get("license"), dict) else {}
        return {"default_branch": str(data.get("default_branch") or "main"), "license": str(license_info.get("spdx_id") or ""),
                "stars": int(data.get("stargazers_count") or 0), "full_name": str(data.get("full_name") or source.full_name)}

    def commit(self, source: Source, ref: str) -> tuple[str, str]:
        data = self.json(f"/repos/{source.owner}/{source.repo}/commits/{quote(ref, safe='')}")
        sha = str(data.get("sha") or "")
        tree = str(((data.get("commit") or {}).get("tree") or {}).get("sha") or "")
        if not SHA_RE.match(sha) or not SHA_RE.match(tree):
            raise PluginSourceError("GitHub не вернул коммит")
        return sha, tree

    def tree(self, source: Source, sha: str, *, recursive: bool) -> list[dict]:
        data = self.json(f"/repos/{source.owner}/{source.repo}/git/trees/{sha}" + ("?recursive=1" if recursive else ""))
        if data.get("truncated"):
            raise PluginSourceError("Папка плагина слишком большая: GitHub отдал неполный список файлов")
        entries = data.get("tree")
        if not isinstance(entries, list):
            raise PluginSourceError("GitHub не вернул список файлов")
        result = []
        for entry in entries:
            if not isinstance(entry, dict) or not SHA_RE.match(str(entry.get("sha") or "")):
                raise PluginSourceError("Неожиданный список файлов GitHub")
            result.append({"path": str(entry.get("path")), "mode": str(entry.get("mode")), "type": str(entry.get("type")),
                           "sha": str(entry["sha"]), "size": int(entry.get("size") or 0)})
        return result

    def subtree(self, source: Source, root_tree: str, path: str) -> str:
        current = root_tree
        for part in [item for item in path.split("/") if item]:
            entry = next((item for item in self.tree(source, current, recursive=False)
                          if item["path"] == part and item["type"] == "tree"), None)
            if entry is None:
                raise PluginSourceError(f"Папка {path} не найдена в репозитории")
            current = entry["sha"]
        return current

    def blob(self, source: Source, entry: dict) -> bytes:
        """Один небольшой файл по его git-хэшу (manifest маркетплейса), содержимое сверяется с хэшем."""
        if entry["size"] > 1024 * 1024:
            raise PluginSourceError("Файл слишком большой")
        data = self.json(f"/repos/{source.owner}/{source.repo}/git/blobs/{entry['sha']}")
        try:
            content = base64.b64decode(str(data.get("content") or ""), validate=False)
        except ValueError as exc:
            raise PluginSourceError("GitHub вернул повреждённый файл") from exc
        if blob_sha(content) != entry["sha"]:
            raise PluginSourceError("Файл не совпадает с закреплённым коммитом")
        return content

    def raw(self, source: Source, commit: str, path: str, entry: dict) -> bytes:
        """Файл коммита с raw.githubusercontent.com: у него нет лимита API, содержимое сверяется с git-хэшем."""
        if entry["size"] > MAX_FILE_BYTES:
            raise PluginSourceError(f"Файл {path} больше {MAX_FILE_BYTES // (1024 * 1024)} МБ")
        url = f"{RAW}/{source.owner}/{source.repo}/{commit}/" + quote(path, safe="/")
        response = self._send(url, stream=True)
        try:
            buffer = io.BytesIO()
            for chunk in response.iter_bytes(256 * 1024):
                buffer.write(chunk)
                self._in_time()
                if buffer.tell() > entry["size"]:
                    break
            data = buffer.getvalue()
        except httpx.HTTPError as exc:
            raise PluginSourceError(f"Обрыв загрузки с GitHub: {type(exc).__name__}") from exc
        finally:
            response.close()
        if len(data) != entry["size"] or blob_sha(data) != entry["sha"]:
            raise PluginSourceError(f"Файл {path} не совпадает с закреплённым коммитом")
        return data

    def archive(self, source: Source, commit: str) -> bytes:
        response = self._send(f"{CODELOAD}/{source.owner}/{source.repo}/tar.gz/{commit}", stream=True)
        try:
            declared = int(response.headers.get("content-length") or 0)
            if declared > MAX_ARCHIVE_BYTES:
                raise PluginSourceError("Архив репозитория слишком большой")
            buffer = io.BytesIO()
            for chunk in response.iter_bytes(1024 * 1024):
                buffer.write(chunk)
                self._in_time()
                if buffer.tell() > MAX_ARCHIVE_BYTES:
                    raise PluginSourceError("Архив репозитория слишком большой")
            return buffer.getvalue()
        except httpx.HTTPError as exc:
            raise PluginSourceError(f"Обрыв загрузки с GitHub: {type(exc).__name__}") from exc
        finally:
            response.close()


def _member_path(name: str) -> list[str]:
    """Части пути члена архива; всё, что может выйти за папку, - отказ всего архива."""
    if not name or "\x00" in name or "\\" in name or name.startswith("/") or re.match(r"^[A-Za-z]:", name):
        raise PluginSourceError("Архив содержит недопустимый путь")
    parts = [part for part in PurePosixPath(name).parts if part != "."]
    if not parts or any(part == ".." for part in parts) or len(parts) > MAX_DEPTH + 1 or len(name) > 1000:
        raise PluginSourceError("Архив содержит путь за пределами папки плагина")
    return parts


WINDOWS_RESERVED = re.compile(r"^(con|prn|aux|nul|com[0-9]|lpt[0-9])(\..*)?$", re.I)


def _portable(relative: str) -> None:
    """Имя, которое запишется на диск без сюрпризов: в Windows нельзя `:` и имена устройств."""
    if os.name != "nt":
        return
    for part in relative.split("/"):
        if re.search(r'[<>:"|?*\x00-\x1f]', part) or WINDOWS_RESERVED.match(part) or part.endswith((".", " ")):
            raise PluginSourceError(f"Файл {relative} нельзя сохранить в Windows: недопустимое имя")


def _checked_name(relative: str, folded: dict[str, str]) -> None:
    """Общие проверки имени файла плагина для архива и для загрузки по файлам."""
    _member_path(relative)
    # на macOS и Windows такие файлы перезапишут друг друга
    if relative.casefold() in folded:
        raise PluginSourceError(f"Файлы {relative} и {folded[relative.casefold()]} различаются только регистром")
    _portable(relative)
    folded[relative.casefold()] = relative


def raw_files(client: GitHub, source: Source, commit: str, entries: list[dict]) -> tuple[dict[str, bytes], list[str]]:
    """Файлы плагина по одному, параллельно; ссылки не скачиваются, подмодули пропускаются."""
    from concurrent.futures import ThreadPoolExecutor

    folded: dict[str, str] = {}
    links, blobs = [], []
    for entry in entries:
        if entry["type"] != "blob":
            continue
        _checked_name(entry["path"], folded)
        if entry["mode"] == "120000":
            links.append(entry["path"])
        else:
            blobs.append(entry)
    prefix = f"{source.path}/" if source.path else ""
    with ThreadPoolExecutor(RAW_WORKERS) as pool:
        data = list(pool.map(lambda entry: client.raw(source, commit, prefix + entry["path"], entry), blobs))
    return {entry["path"]: content for entry, content in zip(blobs, data)}, links


def extract(archive: bytes, prefix: str, expected: dict[str, dict], *, commit: str | None = None) -> tuple[dict[str, bytes], list[str]]:
    """Файлы папки `prefix` из tar.gz GitHub, каждый сверен с ожидаемым git-хэшем.

    Ссылки (symlink/hardlink) не распаковываются, устройства и FIFO ломают установку. В память читается
    не больше лимитов, на диск здесь ничего не пишется.
    """
    files: dict[str, bytes] = {}
    links: list[str] = []
    total = scanned = count = 0
    folded: dict[str, str] = {}
    root: str | None = None
    wanted = [part for part in prefix.split("/") if part]
    try:
        tar = tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz")
    except (tarfile.TarError, OSError, zlib.error) as exc:
        raise PluginSourceError("Архив повреждён") from exc
    with tar:
        # GitHub пишет коммит в глобальный pax-заголовок: архив должен быть именно от этого коммита
        if commit and tar.pax_headers.get("comment") not in {None, commit}:
            raise PluginSourceError("Архив не соответствует закреплённому коммиту")
        try:
            members = iter(tar)
            for member in members:
                scanned += max(member.size, 0)
                count += 1
                if scanned > MAX_UNPACKED_BYTES or count > MAX_MEMBERS:
                    raise PluginSourceError("Архив репозитория слишком большой после распаковки")
                parts = _member_path(member.name)
                if root is None:
                    root = parts[0]
                if parts[0] != root:
                    raise PluginSourceError("Архив содержит несколько корневых папок")
                inner = parts[1:]
                if inner[: len(wanted)] != wanted:
                    continue
                relative = "/".join(inner[len(wanted):])
                if not relative:
                    continue
                if member.isdir():
                    continue
                if member.issym() or member.islnk():
                    links.append(relative)
                    continue
                if not member.isreg():
                    raise PluginSourceError("Архив содержит устройство или особый файл")
                if member.size > MAX_FILE_BYTES:
                    raise PluginSourceError(f"Файл {relative} больше {MAX_FILE_BYTES // (1024 * 1024)} МБ")
                total += member.size
                if total > MAX_TOTAL_BYTES or len(files) >= MAX_FILES:
                    raise PluginSourceError("Плагин больше допустимого размера или числа файлов")
                handle = tar.extractfile(member)
                data = handle.read(MAX_FILE_BYTES + 1) if handle else b""
                if len(data) != member.size:
                    raise PluginSourceError("Архив повреждён")
                entry = expected.get(relative)
                if entry is None or entry["type"] != "blob" or blob_sha(data) != entry["sha"]:
                    raise PluginSourceError(f"Файл {relative} не совпадает с закреплённым коммитом")
                if relative in files:
                    raise PluginSourceError("Архив содержит повторяющиеся файлы")
                _checked_name(relative, folded)
                files[relative] = data
        except (tarfile.TarError, OSError, EOFError, zlib.error) as exc:
            raise PluginSourceError("Архив повреждён") from exc
    return files, links


class NeedsChoice(PluginSourceError):
    """В корне репозитория маркетплейс с несколькими плагинами: человек выбирает один."""

    def __init__(self, choices: list[dict], commit: str):
        super().__init__("В репозитории несколько плагинов: выберите один")
        self.choices = choices
        self.commit = commit


PLUGIN_MARKERS = {".claude-plugin/plugin.json", ".codex-plugin/plugin.json", "plugin.json", ".mcp.json"}
PLUGIN_DIRS = {"skills", "commands", "agents", "hooks"}


def _root_choices(client: GitHub, source: Source, root_tree: str) -> list[dict] | None:
    """Маркетплейс без собственного плагина в корне: список его плагинов без загрузки всего архива."""
    from koddi.plugins.manifest import marketplace

    top = client.tree(source, root_tree, recursive=False)
    names = {entry["path"]: entry for entry in top}
    if any(name in PLUGIN_DIRS and entry["type"] == "tree" for name, entry in names.items()) or "plugin.json" in names \
            or ".mcp.json" in names:
        return None
    found: dict[str, bytes] = {}
    for folder in (".claude-plugin", ".codex-plugin", ".agents"):
        entry = names.get(folder)
        if entry is None or entry["type"] != "tree":
            continue
        for item in client.tree(source, entry["sha"], recursive=True):
            path = f"{folder}/{item['path']}"
            if path in PLUGIN_MARKERS:
                return None
            if item["type"] == "blob" and path in {".claude-plugin/marketplace.json", ".agents/plugins/marketplace.json"}:
                found[path] = client.blob(source, item)
    choices = marketplace(found)
    return choices or None


def fetch(source: Source, *, pinned: str | None = None, github: GitHub | None = None) -> Snapshot:
    """Коммит, дерево и проверенные файлы плагина. `pinned` - ожидаемый коммит из каталога."""
    if pinned is not None and not SHA_RE.match(pinned):
        raise PluginSourceError("Закреплённый коммит должен быть полным SHA из 40 знаков")
    client = github or GitHub()
    client.deadline = time.monotonic() + DEADLINE_SECONDS
    try:
        # без входа GitHub отдаёт только открытые репозитории; описание репозитория нужно лишь для ветки по умолчанию
        info = {"default_branch": "", "license": ""} if pinned or source.ref else client.repo_info(source)
        ref = pinned or source.ref or info["default_branch"]
        commit, root_tree = client.commit(source, ref)
        if pinned and commit != pinned:
            raise PluginSourceError("Коммит на GitHub не совпадает с закреплённым")
        if not source.path:
            choices = _root_choices(client, source, root_tree)
            if choices:
                raise NeedsChoice(choices, commit)
        sub = client.subtree(source, root_tree, source.path) if source.path else root_tree
        entries = client.tree(source, sub, recursive=True)
        if tree_sha(entries) != sub:
            raise PluginSourceError("Список файлов GitHub не сходится с хэшем дерева коммита")
        blobs = [entry for entry in entries if entry["type"] == "blob" and entry["mode"] != "120000"]
        if len(blobs) > MAX_FILES or sum(entry["size"] for entry in blobs) > MAX_TOTAL_BYTES:
            raise PluginSourceError("Плагин больше допустимого размера или числа файлов")
        if source.path and len(blobs) <= RAW_MAX_FILES:
            files, links = raw_files(client, source, commit, entries)
        else:
            expected = {entry["path"]: entry for entry in entries}
            files, links = extract(client.archive(source, commit), source.path, expected, commit=commit)
            # файлы с export-ignore в архив не попадают, но пустой архив при непустом дереве - сбой
            if blobs and not files:
                raise PluginSourceError("Архив GitHub неполный")
        return Snapshot(source=source, commit=commit, tree=sub, default_branch=info["default_branch"],
                        license=info["license"], files=files, skipped_links=links)
    finally:
        if github is None:
            client.close()
