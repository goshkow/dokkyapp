"""Файлы, упомянутые в запросе через «@путь», как в Claude Code: агент сразу получает их содержимое.

Чтение идёт тем же file.read, что у агента: те же правила доступа, маскировка секретов и лимиты. Файл, который
политика не даёт прочитать без вопроса (.env, чужая папка), не подставляется - агент увидит путь и спросит сам."""

from __future__ import annotations

import re
from pathlib import Path

from koddi.tools.runner import ToolRunner

MENTION = re.compile(r"(?:^|(?<=\s))@([^\s@`'\"<>]+)")
MAX_FILES = 5
MAX_CHARS = 20000


def mentioned_paths(text: str) -> list[str]:
    paths: list[str] = []
    for match in MENTION.finditer(text or ""):
        # знак препинания после пути - часть фразы, а не имени файла
        path = match.group(1).rstrip(".,;:!?)»")
        if path and path not in paths:
            paths.append(path)
    return paths[:MAX_FILES]


def mentioned_files(text: str, workspace: Path | None, project_id: int | None) -> str:
    if workspace is None:
        return ""
    paths = mentioned_paths(text)
    if not paths:
        return ""
    runner = ToolRunner(workspace, project_id)
    blocks = []
    for path in paths:
        result = runner.run("file.read", {"path": path})
        if result.get("ok") is False or not isinstance(result.get("content"), str):
            continue
        content = result["content"]
        cut = len(content) > MAX_CHARS or result.get("truncated")
        if len(content) > MAX_CHARS:
            content = content[:MAX_CHARS]
        note = "\n(показано начало файла, остальное прочитайте инструментом)" if cut else ""
        blocks.append(f"### {path}\n```\n{content}\n```{note}")
    if not blocks:
        return ""
    return "Файлы, которые пользователь упомянул через @ (содержимое на момент отправки):\n\n" + "\n\n".join(blocks)
