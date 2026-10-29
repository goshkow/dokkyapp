"""Чистая копия репозитория для публичного репо: без истории и без игнорируемых файлов.

Копирует то, что Git видит в рабочем дереве (отслеживаемое и новое, но не из .gitignore),
в пустую папку. С --init ещё делает там репозиторий с одним коммитом от goshkow.
Не пушит: remote добавляете и пушите сами.

    python scripts/export_public.py ../dokky-public --init
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# на случай, если что-то из этого случайно уйдёт в индекс
# и рабочие записки разработки: требования к серверу и проекты, по которым ещё идёт работа
NEVER = (
    "internal/", "TASKS.md", "PROJECT_CONTEXT.md", "_screens/", ".env",
    "docs/UPDATES_API_SPEC.md", "DOKKY_AGENT_QUALITY_UPGRADE.md", "DOKKY_UB_MODE_ENGINEERING_UPGRADE.md", "docs/UPDATES_CLIENT_DESIGN.md", "docs/PROMPT_CACHE_CLIENT.md", "docs/PLAN_0.2.1.md",
)
AUTHOR = ("goshkow", "goshkow@users.noreply.github.com")


def git(*args: str, cwd: Path = ROOT, check: bool = True) -> str:
    result = subprocess.run(["git", *args], cwd=cwd, check=check, capture_output=True, text=True)
    return result.stdout


def files() -> list[str]:
    raw = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=ROOT, check=True, capture_output=True,
    ).stdout
    names = [n.decode("utf-8") for n in raw.split(b"\0") if n]
    return [n for n in names if (ROOT / n).is_file() and not n.startswith(NEVER)]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("target", type=Path)
    parser.add_argument("--init", action="store_true", help="создать git-репозиторий с одним коммитом")
    args = parser.parse_args()
    target = args.target.resolve()
    if target == ROOT or ROOT in target.parents:
        print("Папка назначения должна быть вне репозитория.")
        return 1
    if target.exists() and any(target.iterdir()):
        print(f"{target} не пустая, выберите другую папку.")
        return 1
    names = files()
    for name in names:
        dest = target / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / name, dest)
    print(f"Скопировано файлов: {len(names)}")
    if not args.init:
        return 0
    git("init", "-q", "-b", "main", cwd=target)
    git("config", "user.name", AUTHOR[0], cwd=target)
    git("config", "user.email", AUTHOR[1], cwd=target)
    git("add", "-A", cwd=target)
    # из Windows бит запуска не копируется: без него scripts/*.sh не запустятся на macOS
    for line in git("ls-files", "-s").splitlines():
        mode, _, rest = line.partition(" ")
        name = rest.split("\t", 1)[-1]
        if mode == "100755" and name in names:
            git("update-index", "--chmod=+x", "--", name, cwd=target)
    check = subprocess.run([sys.executable, str(target / "scripts/check_repository.py")], cwd=target)
    if check.returncode:
        print("Проверка репозитория не прошла, коммит не создан.")
        return check.returncode
    git("commit", "-q", "-m", "Первый релиз Докки", cwd=target)
    print(f"Готово: {target}\nДальше: git remote add origin <ssh-адрес> && git push -u origin main")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
