"""Разбор одного файла для карты проекта: краткое описание, символы, импорты, маршруты API и ссылки.

Только чистые функции без доступа к диску: на вход относительный путь и байты, на выход словарь.
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import PurePosixPath

from koddi.logs.service import redact
from koddi.projectmap.chunks import chunk_ranges

# новая версия разбора перечитывает все файлы, даже если mtime не менялся
# 2: фрагменты для поиска контекста, признак теста и вызываемые имена
PARSER_VERSION = 2
MAX_PARSE_BYTES = 512 * 1024
MAX_SYMBOLS = 80
MAX_IMPORTS = 120
MAX_ROUTES = 120
MAX_CALLS = 80
MAX_LINKS = 80
MAX_REFS = 160
TEST_PATH = re.compile(r"(?i)(^|/)(tests?|__tests__|spec|specs|e2e)/|(^|/)test_[^/]+\.py$|_test\.(py|go)$|\.(test|spec)\.[cm]?[jt]sx?$|Tests?\.(cs|swift|kt|java)$")
JS_CALL = re.compile(r"(?<![\w$.])([A-Za-z_$][\w$]*)\s*\(|\.([A-Za-z_$][\w$]*)\s*\(")
JS_KEYWORDS = {"if", "for", "while", "switch", "catch", "function", "return", "typeof", "await", "super", "import", "new",
               "as", "is", "in", "of", "void", "delete", "case", "satisfies", "keyof"}
MAX_SUMMARY = 200

LANGUAGES = {
    ".py": "python", ".pyi": "python",
    ".ts": "typescript", ".tsx": "typescript", ".mts": "typescript", ".cts": "typescript",
    ".js": "javascript", ".jsx": "javascript", ".mjs": "javascript", ".cjs": "javascript",
    ".vue": "vue", ".svelte": "svelte",
    ".go": "go", ".rs": "rust", ".java": "java", ".kt": "kotlin", ".kts": "kotlin", ".swift": "swift",
    ".cs": "csharp", ".fs": "fsharp", ".c": "c", ".h": "c", ".cc": "cpp", ".cpp": "cpp", ".cxx": "cpp",
    ".hpp": "cpp", ".hh": "cpp", ".m": "objc", ".mm": "objc", ".rb": "ruby", ".php": "php", ".lua": "lua",
    ".dart": "dart", ".scala": "scala", ".ex": "elixir", ".exs": "elixir", ".zig": "zig", ".r": "r",
    ".sh": "shell", ".bash": "shell", ".zsh": "shell", ".ps1": "powershell", ".psm1": "powershell",
    ".bat": "batch", ".cmd": "batch", ".sql": "sql",
    ".md": "markdown", ".mdx": "markdown", ".markdown": "markdown", ".rst": "text", ".txt": "text",
    ".css": "css", ".scss": "css", ".sass": "css", ".less": "css", ".html": "html", ".htm": "html",
    ".json": "json", ".toml": "toml", ".yaml": "yaml", ".yml": "yaml", ".ini": "config", ".cfg": "config",
    ".xml": "xml", ".gradle": "gradle", ".proto": "proto", ".graphql": "graphql", ".gql": "graphql",
}
FILE_NAMES = {
    "dockerfile": "docker", "makefile": "make", "cmakelists.txt": "cmake", "procfile": "config",
    "gemfile": "ruby", "rakefile": "ruby", "jenkinsfile": "groovy", "justfile": "make", "go.mod": "gomod",
}
# служебные файлы без смысла для карты и файлы, где обычно лежат ключи
SKIP_NAMES = re.compile(
    r"(?i)^(package-lock\.json|yarn\.lock|pnpm-lock\.yaml|poetry\.lock|cargo\.lock|composer\.lock|gemfile\.lock|"
    r"uv\.lock|.*\.min\.(js|css)|.*\.map|\.env(\..*)?|.*\.(pem|key|p12|pfx|keystore|jks)|id_(rsa|dsa|ecdsa|ed25519).*|"
    r"\.npmrc|\.pypirc|\.netrc|credentials(\..*)?|secrets?\.(json|ya?ml|toml))$"
)
ALLOWED_SECRET_EXAMPLES = re.compile(r"(?i)^\.env\.(example|sample|template)$")
JS_LIKE = {"typescript", "javascript", "vue", "svelte"}


def language_of(path: str) -> str | None:
    """Язык по имени файла или None, если файл не входит в карту."""
    name = PurePosixPath(path).name
    if SKIP_NAMES.match(name) and not ALLOWED_SECRET_EXAMPLES.match(name):
        return None
    lowered = name.lower()
    if lowered in FILE_NAMES:
        return FILE_NAMES[lowered]
    if ALLOWED_SECRET_EXAMPLES.match(name):
        return "config"
    return LANGUAGES.get(PurePosixPath(lowered).suffix)


def _clean(text: str, limit: int = MAX_SUMMARY) -> str:
    text = re.sub(r"\s+", " ", text).strip().strip("-=*#/ ").strip()
    return redact(text[: limit - 1] + "…" if len(text) > limit else text)


def _first_comment(lines: list[str], markers: tuple[str, ...]) -> str:
    """Первая осмысленная строка комментария в начале файла (до кода)."""
    block = False
    for raw in lines[:40]:
        line = raw.strip()
        if not line or line.startswith("#!") or re.match(r"#.*coding[:=]", line):
            continue
        if line.startswith(("/*", "/**")):
            block = True
            line = line.lstrip("/*").strip()
            if line.endswith("*/"):
                line = line[:-2].strip()
                block = False
            if line:
                return _clean(line)
            continue
        if block:
            if line.endswith("*/"):
                line = line[:-2]
                block = False
            line = line.lstrip("*").strip()
            if line:
                return _clean(line)
            continue
        for marker in markers:
            if line.startswith(marker):
                text = line[len(marker):].strip()
                if text and not re.match(r"(?i)(eslint|prettier|@ts-|type:|noqa|pragma|region|endregion|-\*-)", text):
                    return _clean(text)
                break
        else:
            if not line.startswith(("import ", "from ", "use ", "package ", "'use ", '"use ', "<?php", "@echo")):
                return ""
    return ""


def _line_of(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def _python(text: str, info: dict) -> None:
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        info["parse_error"] = True
        _generic(text, info, "python")
        return
    doc = ast.get_docstring(tree)
    if doc:
        info["summary"] = _clean(doc.strip().splitlines()[0])
    else:
        info["summary"] = _first_comment(text.splitlines(), ("#",))
    symbols = []
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            symbols.append([node.name, "class", node.lineno])
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and not item.name.startswith("__"):
                    symbols.append([f"{node.name}.{item.name}", "method", item.lineno])
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            symbols.append([node.name, "def", node.lineno])
        elif isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            name = node.targets[0].id
            if name.isupper() and len(name) > 2:
                symbols.append([name, "const", node.lineno])
    info["symbols"] = symbols[:MAX_SYMBOLS]
    info["chunks"] = chunk_ranges("python", text, tree)
    refs: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = node.func.id if isinstance(node.func, ast.Name) else node.func.attr if isinstance(node.func, ast.Attribute) else None
            if name and name not in refs:
                refs.append(name)
                if len(refs) >= MAX_REFS:
                    break
    info["refs"] = refs
    imports = []
    prefixes: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend({"m": alias.name, "lv": 0} for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imports.append({"m": node.module or "", "lv": node.level, "n": [a.name for a in node.names if a.name != "*"][:20]})
        elif (
            isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name)
            and isinstance(node.value, ast.Call)
        ):
            # APIRouter(prefix=...) и Blueprint(url_prefix=...) дают префикс маршрутам этой переменной
            for keyword in node.value.keywords:
                if keyword.arg in {"prefix", "url_prefix"} and isinstance(keyword.value, ast.Constant) and isinstance(keyword.value.value, str):
                    prefixes[node.targets[0].id] = keyword.value.value.rstrip("/")
    info["imports"] = imports[:MAX_IMPORTS]
    routes = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            if not (isinstance(decorator, ast.Call) and isinstance(decorator.func, ast.Attribute) and decorator.args):
                continue
            verb = decorator.func.attr.lower()
            first = decorator.args[0]
            if verb not in {"get", "post", "put", "delete", "patch", "head", "options", "route", "api_route", "websocket"}:
                continue
            owner = decorator.func.value.id if isinstance(decorator.func.value, ast.Name) else ""
            # @router.post("") у роутера с prefix - маршрут самого префикса
            if not (isinstance(first, ast.Constant) and isinstance(first.value, str)
                    and (first.value.startswith("/") or (first.value == "" and owner in prefixes))):
                continue
            path = prefixes.get(owner, "") + first.value
            methods = [verb.upper()] if verb not in {"route", "api_route"} else ["GET"]
            for keyword in decorator.keywords:
                if keyword.arg == "methods" and isinstance(keyword.value, (ast.List, ast.Tuple)):
                    methods = [str(item.value).upper() for item in keyword.value.elts if isinstance(item, ast.Constant)] or methods
            if verb == "websocket":
                methods = ["WS"]
            routes.extend({"m": method, "p": path, "l": node.lineno, "h": node.name} for method in methods)
    info["routes"] = routes[:MAX_ROUTES]


JS_IMPORT = re.compile(
    r"""(?:^|[;\s])(?:import\s+(?:type\s+)?(?:[^'";]*?\s+from\s+)?|export\s+(?:type\s+)?[^'";]*?\s+from\s+)['"]([^'"\n]+)['"]"""
    r"""|\b(?:require|import)\(\s*['"]([^'"\n]+)['"]\s*\)""",
    re.M,
)
JS_SYMBOL = re.compile(
    r"^(?:export\s+(?:default\s+)?)?(?:declare\s+)?(?:abstract\s+)?(?:async\s+)?"
    r"(function\*?|class|interface|type|enum|const|let|var)\s+([A-Za-z_$][\w$]*)",
    re.M,
)
JS_ROUTE = re.compile(r"""\b(?:app|router|server|api|routes)\.(get|post|put|delete|patch|all)\(\s*['"`](/[^'"`\s]*)['"`]""")
# адреса локального API в коде интерфейса: так связываются экраны и обработчики сервера
API_CALL = re.compile(r"""['"`](/api/[^'"`\s?#]*)""")


def _javascript(text: str, info: dict) -> None:
    lines = text.splitlines()
    info["summary"] = _first_comment(lines, ("//",))
    imports = []
    for match in JS_IMPORT.finditer(text):
        spec = match.group(1) or match.group(2)
        if spec and spec not in imports:
            imports.append(spec)
    info["imports"] = imports[:MAX_IMPORTS]
    symbols = []
    for match in JS_SYMBOL.finditer(text):
        kind, name = match.groups()
        line_text = text[match.start(): text.find("\n", match.start())]
        exported = line_text.startswith("export")
        # обычные const/let попадают в карту, только если экспортированы
        if kind in {"const", "let", "var"} and not exported:
            continue
        kind = {"function*": "function"}.get(kind, kind)
        symbols.append([name, ("export " if exported else "") + kind, _line_of(text, match.start())])
    info["symbols"] = symbols[:MAX_SYMBOLS]
    from koddi.codeintel import jslex

    refs: list[str] = []
    for match in JS_CALL.finditer(jslex.mask(text)):
        name = match.group(1) or match.group(2)
        if name not in JS_KEYWORDS and name not in refs:
            refs.append(name)
            if len(refs) >= MAX_REFS:
                break
    info["refs"] = refs
    info["routes"] = [
        {"m": verb.upper() if verb != "all" else "ANY", "p": path, "l": _line_of(text, match.start())}
        for match in JS_ROUTE.finditer(text)
        for verb, path in [match.groups()]
    ][:MAX_ROUTES]
    _api_calls(text, info)


def _api_calls(text: str, info: dict) -> None:
    calls = []
    for match in API_CALL.finditer(text):
        path = re.sub(r"\$\{[^}]*\}", "{}", match.group(1)).rstrip("/") or "/"
        if path not in calls:
            calls.append(path)
    if calls:
        info["calls"] = calls[:MAX_CALLS]


GENERIC_SYMBOLS = {
    "go": re.compile(r"^(?:func\s+(?:\([^)]*\)\s*)?([A-Za-z_]\w*)|type\s+([A-Za-z_]\w*)\s+(?:struct|interface))", re.M),
    "rust": re.compile(r"^\s*(?:pub(?:\([^)]*\))?\s+)?(?:async\s+)?(?:fn|struct|enum|trait|mod|impl(?:<[^>]*>)?)\s+([A-Za-z_]\w*)", re.M),
    "swift": re.compile(r"^\s*(?:public\s+|private\s+|internal\s+|open\s+|final\s+|@\w+\s+)*(?:class|struct|enum|protocol|extension|func|actor)\s+([A-Za-z_]\w*)", re.M),
    "kotlin": re.compile(r"^\s*(?:public\s+|private\s+|internal\s+|data\s+|sealed\s+|open\s+|abstract\s+|suspend\s+)*(?:class|interface|object|fun|enum\s+class)\s+([A-Za-z_]\w*)", re.M),
    "java": re.compile(r"^\s*(?:public\s+|private\s+|protected\s+|static\s+|final\s+|abstract\s+)*(?:class|interface|enum|record)\s+([A-Za-z_]\w*)", re.M),
    "csharp": re.compile(r"^\s*(?:public\s+|private\s+|internal\s+|protected\s+|static\s+|sealed\s+|partial\s+|abstract\s+)*(?:class|interface|enum|struct|record)\s+([A-Za-z_]\w*)", re.M),
    "ruby": re.compile(r"^\s*(?:class|module|def)\s+([A-Za-z_][\w.:?!]*)", re.M),
    "php": re.compile(r"^\s*(?:abstract\s+|final\s+)?(?:class|interface|trait|function)\s+([A-Za-z_]\w*)", re.M),
    "dart": re.compile(r"^\s*(?:abstract\s+)?(?:class|mixin|enum|extension)\s+([A-Za-z_]\w*)", re.M),
    "scala": re.compile(r"^\s*(?:case\s+)?(?:class|object|trait|def)\s+([A-Za-z_]\w*)", re.M),
    "shell": re.compile(r"^\s*(?:function\s+)?([A-Za-z_][\w-]*)\s*\(\)\s*\{", re.M),
    "powershell": re.compile(r"^\s*function\s+([A-Za-z_][\w-]*)", re.M | re.I),
    "sql": re.compile(r"(?i)\bcreate\s+(?:virtual\s+|unique\s+)?(?:table|view|index|trigger)\s+(?:if\s+not\s+exists\s+)?[\"`\[]?([A-Za-z_][\w.]*)"),
    "c": re.compile(r"^\s*(?:typedef\s+)?(?:struct|enum|union)\s+([A-Za-z_]\w*)\s*\{", re.M),
    "cpp": re.compile(r"^\s*(?:class|struct|namespace|enum(?:\s+class)?)\s+([A-Za-z_]\w*)", re.M),
    "objc": re.compile(r"^\s*@(?:interface|implementation|protocol)\s+([A-Za-z_]\w*)", re.M),
    "python": re.compile(r"^(?:class|def|async\s+def)\s+([A-Za-z_]\w*)", re.M),
}
GENERIC_IMPORTS = {
    "go": re.compile(r'^\s*(?:import\s+)?(?:[\w.]+\s+)?"([\w./-]+)"', re.M),
    "rust": re.compile(r"^\s*(?:pub\s+)?(?:use\s+(crate::[\w:]+)|mod\s+(\w+)\s*;)", re.M),
    "c": re.compile(r'^\s*#\s*(?:include|import)\s+"([^"]+)"', re.M),
    "cpp": re.compile(r'^\s*#\s*(?:include|import)\s+"([^"]+)"', re.M),
    "objc": re.compile(r'^\s*#\s*(?:include|import)\s+"([^"]+)"', re.M),
    "css": re.compile(r"""@(?:import|use|forward)\s+(?:url\()?['"]([^'"]+)['"]"""),
    "ruby": re.compile(r"""^\s*require_relative\s+['"]([^'"]+)['"]""", re.M),
    "shell": re.compile(r"""^\s*(?:source|\.)\s+['"]?([\w./-]+\.(?:sh|bash|zsh))""", re.M),
}
SPRING_ROUTE = re.compile(r'@(Get|Post|Put|Delete|Patch|Request)Mapping\(\s*(?:value\s*=\s*|path\s*=\s*)?"(/[^"]*)"')
GO_ROUTE = re.compile(r'\.(GET|POST|PUT|DELETE|PATCH|HandleFunc|Handle)\(\s*"(/[^"]*)"')
COMMENT_MARKERS = {
    "ruby": ("#",), "shell": ("#",), "powershell": ("#",), "python": ("#",), "toml": ("#",), "yaml": ("#",),
    "config": ("#", ";"), "sql": ("--",), "lua": ("--",), "batch": ("REM ", "::"), "make": ("#",), "docker": ("#",),
    "cmake": ("#",), "r": ("#",), "elixir": ("#",),
}


def _generic(text: str, info: dict, lang: str) -> None:
    lines = text.splitlines()
    info["summary"] = _first_comment(lines, COMMENT_MARKERS.get(lang, ("//", "#")))
    pattern = GENERIC_SYMBOLS.get(lang)
    if pattern:
        symbols = []
        for match in pattern.finditer(text):
            name = next((group for group in match.groups() if group), None)
            if name:
                symbols.append([name, "table" if lang == "sql" else "symbol", _line_of(text, match.start())])
        info["symbols"] = symbols[:MAX_SYMBOLS]
    pattern = GENERIC_IMPORTS.get(lang)
    if pattern:
        imports = []
        for match in pattern.finditer(text):
            spec = next((group for group in match.groups() if group), None)
            if spec and spec not in imports:
                imports.append(spec)
        info["imports"] = imports[:MAX_IMPORTS]
    routes = []
    for regex in (SPRING_ROUTE, GO_ROUTE):
        for match in regex.finditer(text):
            verb, path = match.groups()
            verb = verb.upper()
            routes.append({"m": verb if verb in {"GET", "POST", "PUT", "DELETE", "PATCH"} else "ANY", "p": path, "l": _line_of(text, match.start())})
    if routes:
        info["routes"] = routes[:MAX_ROUTES]


MD_HEADING = re.compile(r"^(#{1,3})\s+(.+?)\s*#*\s*$", re.M)
MD_WIKILINK = re.compile(r"\[\[([^\]|#\n]+)(?:#[^\]|\n]*)?(?:\|[^\]\n]*)?\]\]")
MD_LINK = re.compile(r"\]\(\s*<?([^)\s>]+)>?(?:\s+\"[^\"]*\")?\s*\)")


def _markdown(text: str, info: dict) -> None:
    headings = [(match.group(1), match.group(2), _line_of(text, match.start())) for match in MD_HEADING.finditer(text)]
    if headings:
        info["summary"] = _clean(headings[0][1])
    else:
        info["summary"] = next((_clean(line) for line in text.splitlines() if line.strip()), "")
    info["symbols"] = [[_clean(title, 120), "h" + str(len(level)), line] for level, title, line in headings[:MAX_SYMBOLS]]
    links = []
    for match in MD_WIKILINK.finditer(text):
        links.append("[[" + match.group(1).strip())
    for match in MD_LINK.finditer(text):
        target = match.group(1)
        if re.match(r"(?i)^(https?:|mailto:|tel:|data:|#)", target):
            continue
        links.append(target.split("#")[0])
    info["links"] = list(dict.fromkeys(link for link in links if link))[:MAX_LINKS]


def _html(text: str, info: dict) -> None:
    title = re.search(r"(?is)<title>(.*?)</title>", text)
    info["summary"] = _clean(title.group(1)) if title else ""
    imports = []
    for match in re.finditer(r"""(?i)<(?:script|link|img)\b[^>]*?\b(?:src|href)\s*=\s*['"]([^'"#?]+)""", text):
        spec = match.group(1)
        if not re.match(r"(?i)^(https?:|//|data:|mailto:)", spec) and spec not in imports:
            imports.append(spec)
    info["imports"] = imports[:MAX_IMPORTS]
    _api_calls(text, info)


def _json(path: str, text: str, info: dict) -> None:
    if PurePosixPath(path).name.lower() != "package.json":
        return
    try:
        value = json.loads(text)
    except ValueError:
        return
    if not isinstance(value, dict):
        return
    name = value.get("name") if isinstance(value.get("name"), str) else ""
    description = value.get("description") if isinstance(value.get("description"), str) else ""
    info["summary"] = _clean(" - ".join(part for part in (f"npm package {name}".strip(), description) if part))
    scripts = value.get("scripts") if isinstance(value.get("scripts"), dict) else {}
    info["symbols"] = [[key, "script", 0] for key in list(scripts)[:30]]
    deps = []
    for key in ("dependencies", "devDependencies", "peerDependencies"):
        if isinstance(value.get(key), dict):
            deps.extend(value[key])
    if deps:
        info["packages"] = sorted(set(deps))[:60]


def _toml(path: str, text: str, info: dict) -> None:
    name = re.search(r'(?m)^name\s*=\s*"([^"]+)"', text)
    description = re.search(r'(?m)^description\s*=\s*"([^"]+)"', text)
    parts = [part.group(1) for part in (name, description) if part]
    info["summary"] = _clean(" - ".join(parts)) if parts else _first_comment(text.splitlines(), ("#",))
    scripts = re.search(r"(?ms)^\[(?:project\.scripts|tool\.poetry\.scripts)\]\s*\n(.*?)(?:^\[|\Z)", text)
    if scripts:
        info["symbols"] = [[key, "script", 0] for key in re.findall(r'(?m)^([\w.-]+)\s*=', scripts.group(1))[:30]]


def _yaml(text: str, info: dict) -> None:
    name = re.search(r"(?m)^name:\s*['\"]?([^'\"\n]+)", text)
    info["summary"] = _clean(name.group(1)) if name else _first_comment(text.splitlines(), ("#",))


def parse_file(path: str, data: bytes) -> dict:
    """Сведения о файле для карты. Неподдерживаемый, двоичный или слишком большой файл даёт только язык и размер."""
    lang = language_of(path) or "text"
    info: dict = {"lang": lang}
    if len(data) > MAX_PARSE_BYTES or b"\x00" in data[:8192]:
        info["skipped"] = "binary" if b"\x00" in data[:8192] else "large"
        return info
    text = data.decode("utf-8", errors="replace")
    info["lines"] = text.count("\n") + (0 if text.endswith("\n") or not text else 1)
    if lang == "python":
        _python(text, info)
    elif lang in JS_LIKE:
        _javascript(text, info)
    elif lang == "markdown":
        _markdown(text, info)
    elif lang == "html":
        _html(text, info)
    elif lang == "json":
        _json(path, text, info)
    elif lang == "toml":
        _toml(path, text, info)
    elif lang == "yaml":
        _yaml(text, info)
    elif lang == "gomod":
        module = re.search(r"(?m)^module\s+(\S+)", text)
        if module:
            info["go_module"] = module.group(1)
            info["summary"] = _clean("Go module " + module.group(1))
    else:
        _generic(text, info, lang)
    if "chunks" not in info:
        info["chunks"] = chunk_ranges("typescript" if lang in JS_LIKE else lang, text)
    if TEST_PATH.search(path):
        info["test"] = True
    return {key: value for key, value in info.items() if value not in ("", [], None)}
