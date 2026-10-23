from __future__ import annotations

import hashlib
from pathlib import Path

from koddi.policy.paths import PathAccessError, WorkspacePaths


# правила проекта: читаем все распространённые файлы правил, которые люди кладут в репозиторий
INSTRUCTION_FILES = ("AGENTS.md", "LOCAL_AGENT.md", "CLAUDE.md", "GEMINI.md", ".cursorrules")
# только в корне проекта: файлы и папки правил других помощников
ROOT_INSTRUCTION_FILES = (".github/copilot-instructions.md", ".claude/CLAUDE.md")
ROOT_INSTRUCTION_FOLDERS = (".kilocode/rules", ".roo/rules", ".cursor/rules")
MAX_FOLDER_RULES = 12


def scoped_instructions(
    workspace: Path, target: str = ".", *, directory: bool = True, include_root: bool = True
) -> list[dict]:
    paths = WorkspacePaths(workspace)
    resolved = paths.resolve(target)
    parent = resolved if directory else resolved.parent
    relative = parent.relative_to(paths.root)
    directories = [paths.root]
    for part in relative.parts:
        directories.append(directories[-1] / part)
    records = []
    for folder in directories:
        if folder == paths.root and not include_root:
            continue
        scope = folder.relative_to(paths.root).as_posix()
        names = list(INSTRUCTION_FILES)
        if folder == paths.root:
            names.extend(ROOT_INSTRUCTION_FILES)
            for rules in ROOT_INSTRUCTION_FOLDERS:
                directory_path = folder / rules
                if directory_path.is_dir() and not directory_path.is_symlink():
                    names.extend(
                        item.relative_to(folder).as_posix()
                        for item in sorted(directory_path.iterdir())[:MAX_FOLDER_RULES]
                        if item.suffix.lower() in {".md", ".mdc", ".txt"}
                    )
        for name in names:
            candidate = folder / name
            if not candidate.exists() and not candidate.is_symlink():
                continue
            rule_path = candidate.relative_to(paths.root).as_posix()
            try:
                safe = paths.resolve(str(candidate))
            except PathAccessError:
                records.append(
                    {
                        "path": rule_path,
                        "scope": scope,
                        "error": "Instruction file is excluded by workspace access policy.",
                    }
                )
                continue
            if not safe.is_file():
                continue
            content = safe.read_text(encoding="utf-8-sig", errors="replace")
            records.append(
                {
                    "path": rule_path,
                    "scope": scope,
                    "content": content,
                    "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
                }
            )
    return records


def format_instructions(records: list[dict]) -> str:
    return "\n\n".join(
        f"## {item['path']} (scope: {item['scope']})\n" + item.get("content", item.get("error", ""))
        for item in records
    )
