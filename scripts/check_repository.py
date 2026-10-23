"""Проверяет файлы в Git и не печатает найденные секреты."""

from __future__ import annotations

import re
import argparse
import subprocess
from pathlib import Path, PurePosixPath


ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN_DIRS = {
    "__pycache__", ".pytest_cache", ".ruff_cache", "node_modules", ".venv", "venv",
    ".coduvo-data", ".codara-data", ".koddi-data", "build", "dist", "htmlcov",
    "test-results", "playwright-report",
}
FORBIDDEN_SUFFIXES = {
    ".pyc", ".pyo", ".sqlite", ".sqlite3", ".db", ".log", ".exe", ".dll",
    ".pdb", ".gguf", ".part", ".key", ".pem",
}
SECRET = re.compile(
    rb"gh[pousr]_[A-Za-z0-9]{20,}"
    rb"|sk-[A-Za-z0-9_-]{24,}"
    rb"|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"
    rb"|eyJ[A-Za-z0-9_-]{25,}\.[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}"
)


def is_runtime_path(name: str) -> bool:
    path = PurePosixPath(name)
    return (
        any(part in FORBIDDEN_DIRS or part.startswith(("dist_", "build_", ".test-data-"))
            or part.endswith(".egg-info") for part in path.parts[:-1])
        or path.suffix.lower() in FORBIDDEN_SUFFIXES
        or (path.name.startswith(".env") and path.name != ".env.example")
        or ".sqlite3-" in path.name
        or ".db-" in path.name
    )


def main() -> int:
    parser=argparse.ArgumentParser()
    parser.add_argument('--working-tree',action='store_true')
    args=parser.parse_args()
    command=['git','ls-files','--cached','-z']
    if args.working_tree:
        command=['git','ls-files','--cached','--others','--exclude-standard','-z']
    result = subprocess.run(
        command, cwd=ROOT, check=True, capture_output=True
    )
    paths = [name.decode("utf-8") for name in result.stdout.split(b"\0") if name]
    if not paths:
        print("В индексе Git нет файлов для проверки.")
        return 1
    errors = []
    for name in paths:
        if is_runtime_path(name):
            errors.append(f"{name}: служебный или личный файл")
            continue
        if args.working_tree:
            path=ROOT/name
            if not path.is_file():
                continue
            blob=path.read_bytes()
        else:
            blob = subprocess.run(
                ["git", "show", f":{name}"], cwd=ROOT, check=True, capture_output=True
            ).stdout
        if len(blob) > 10 * 1024 * 1024:
            errors.append(f"{name}: файл больше 10 МиБ; проверьте необходимость публикации")
        if SECRET.search(blob):
            errors.append(f"{name}: обнаружен возможный секрет; значение скрыто")
    if errors:
        print("Проверка репозитория не пройдена:\n" + "\n".join(errors))
        return 1
    print(f"Проверка репозитория пройдена: {len(paths)} файлов, служебных файлов и узнаваемых секретов нет.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
