from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable


@dataclass
class FilePatch:
    source: str | None
    destination: str | None
    hunks: list[list[str]]
    unified: bool = False


def parse_patch(text: str) -> list[FilePatch]:
    lines = text.splitlines()
    if not lines:
        raise ValueError("empty patch")
    patches = []
    if lines[0] == "*** Begin Patch":
        if lines[-1] != "*** End Patch":
            raise ValueError("patch must end with *** End Patch")
        i = 1
        while i < len(lines) - 1:
            header = lines[i]
            i += 1
            match = re.fullmatch(r"\*\*\* (Add|Update|Delete) File: (.+)", header)
            if not match:
                raise ValueError(f"invalid patch header: {header}")
            operation, path = match.groups()
            source = None if operation == "Add" else path
            destination = None if operation == "Delete" else path
            if i < len(lines) and lines[i].startswith("*** Move to: "):
                if operation != "Update":
                    raise ValueError("only Update File can be moved")
                destination = lines[i][13:]
                i += 1
            body = []
            while i < len(lines) - 1 and not re.match(
                r"\*\*\* (?:Add|Update|Delete) File: ", lines[i]
            ):
                body.append(lines[i])
                i += 1
            if operation == "Delete" and body:
                raise ValueError("Delete File cannot contain hunks")
            if operation == "Add" and any(not line.startswith("+") for line in body):
                raise ValueError("Add File lines must start with +")
            hunks: list[list[str]] = []
            if operation == "Update":
                for line in body:
                    if line == "@@" or line.startswith("@@ "):
                        hunks.append([line])
                    elif hunks and (line.startswith((" ", "+", "-")) or line == "*** End of File"):
                        hunks[-1].append(line)
                    else:
                        raise ValueError("Update File requires @@ hunks with prefixed lines")
                if not hunks:
                    raise ValueError("Update File has no hunks")
            else:
                hunks = [body] if operation == "Add" else []
            patches.append(FilePatch(source, destination, hunks))
    else:
        i = 0
        while i < len(lines):
            if lines[i].startswith(("diff --git ", "index ")):
                i += 1
                continue
            if (
                not lines[i].startswith("--- ")
                or i + 1 >= len(lines)
                or not lines[i + 1].startswith("+++ ")
            ):
                raise ValueError("unified patch requires ---/+++ headers")
            old = lines[i][4:].split("\t", 1)[0]
            new = lines[i + 1][4:].split("\t", 1)[0]
            source = None if old == "/dev/null" else old.removeprefix("a/")
            destination = None if new == "/dev/null" else new.removeprefix("b/")
            i += 2
            hunks = []
            while i < len(lines) and not lines[i].startswith(("--- ", "diff --git ")):
                if not re.fullmatch(r"@@ -\d+(?:,\d+)? \+\d+(?:,\d+)? @@.*", lines[i]):
                    raise ValueError("invalid unified hunk header")
                hunk = [lines[i]]
                counts = re.match(r"@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", lines[i])
                old_count = int(counts[2] or 1)
                new_count = int(counts[4] or 1)
                i += 1
                seen_old = seen_new = 0
                while i < len(lines):
                    line = lines[i]
                    if line == "\\ No newline at end of file":
                        if len(hunk) == 1 or hunk[-1].startswith("\\"):
                            raise ValueError("invalid no-newline marker")
                        hunk.append(line)
                        i += 1
                        continue
                    if seen_old == old_count and seen_new == new_count:
                        break
                    if not line.startswith((" ", "+", "-")):
                        raise ValueError("invalid unified hunk body")
                    seen_old += line[0] in " -"
                    seen_new += line[0] in " +"
                    if seen_old > old_count or seen_new > new_count:
                        raise ValueError("unified hunk counts do not match")
                    hunk.append(line)
                    i += 1
                if (seen_old, seen_new) != (old_count, new_count):
                    raise ValueError("truncated unified hunk")
                hunks.append(hunk)
            if not hunks or (source is None and destination is None):
                raise ValueError("unified patch has no applicable hunks")
            patches.append(FilePatch(source, destination, hunks, unified=True))
    if not patches:
        raise ValueError("patch contains no files")
    return patches


def _apply_hunks(patch: FilePatch, before: str) -> str:
    newline = "\r\n" if "\r\n" in before else "\n"
    original = before.replace("\r\n", "\n").splitlines(keepends=True)
    output: list[str] = []
    cursor = 0
    for hunk in patch.hunks:
        old: list[str] = []
        new: list[str] = []
        body = hunk[1:]
        eof = bool(body and body[-1] == "*** End of File")
        if eof:
            body = body[:-1]
        for index, line in enumerate(body):
            if line.startswith("\\"):
                continue
            if not line.startswith((" ", "-", "+")):
                raise ValueError("invalid hunk line")
            no_newline = index + 1 < len(body) and body[index + 1] == "\\ No newline at end of file"
            value = line[1:] + ("" if no_newline else "\n")
            if line[0] in " -":
                old.append(value)
            if line[0] in " +":
                new.append(value)
        if old == new:
            raise ValueError("patch hunk has no changes")
        if patch.unified:
            match = re.match(r"@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", hunk[0])
            start = int(match[1]) - (1 if int(match[2] or 1) else 0)
            new_start = int(match[3]) - (1 if int(match[4] or 1) else 0)
            if start < cursor or start > len(original):
                raise ValueError("overlapping or out-of-range unified hunk")
            if new_start != len(output) + start - cursor:
                raise ValueError("inconsistent unified new line position")
            if original[start : start + len(old)] != old:
                raise ValueError("unified patch context does not match file")
        else:
            anchor = hunk[0][3:] if hunk[0].startswith("@@ ") else ""
            search_start = cursor
            if anchor:
                anchors = [
                    i for i in range(cursor, len(original)) if original[i].rstrip("\n") == anchor
                ]
                if len(anchors) != 1:
                    raise ValueError("patch section anchor is missing or ambiguous")
                search_start = anchors[0] + 1
            candidates = []
            for start in range(search_start, len(original) - len(old) + 1):
                # в apply_patch нет маркеров «без перевода строки», сравниваем строки целиком
                if [line.rstrip("\n") for line in original[start : start + len(old)]] == [
                    line.rstrip("\n") for line in old
                ] and (not eof or start + len(old) == len(original)):
                    candidates.append(start)
            if len(candidates) != 1:
                raise ValueError("patch context is missing or ambiguous")
            start = candidates[0]
            if start + len(old) == len(original) and before and not before.endswith("\n") and new:
                new[-1] = new[-1].removesuffix("\n")
        output.extend(original[cursor:start])
        output.extend(new)
        cursor = start + len(old)
    output.extend(original[cursor:])
    return "".join(output).replace("\n", newline)


def prepare_patch(
    patches: list[FilePatch], read: Callable[[str], str | None]
) -> dict[str, tuple[str | None, str | None]]:
    changes: dict[str, tuple[str | None, str | None]] = {}
    for patch in patches:
        before = read(patch.source) if patch.source else None
        if patch.source and before is None:
            raise ValueError(f"patch source does not exist: {patch.source}")
        if (
            patch.destination
            and patch.destination != patch.source
            and read(patch.destination) is not None
        ):
            raise ValueError(f"patch destination already exists: {patch.destination}")
        if patch.source is None:
            after = "".join(line[1:] + "\n" for line in patch.hunks[0])
        elif not patch.hunks:
            after = None
        else:
            after = _apply_hunks(patch, before)
        if patch.destination is None and after not in {None, ""}:
            raise ValueError("delete patch does not remove the whole file")
        paths = {path for path in (patch.source, patch.destination) if path}
        if paths & changes.keys():
            raise ValueError("a patch may touch each path only once")
        if patch.source == patch.destination:
            changes[patch.source] = (before, after)
        else:
            if patch.source:
                changes[patch.source] = (before, None)
            if patch.destination:
                changes[patch.destination] = (None, after)
    return changes
