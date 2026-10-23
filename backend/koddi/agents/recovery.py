from __future__ import annotations

import json
import os
import subprocess
import sys
import ctypes

if os.name == "nt":
    from ctypes import wintypes

from koddi.storage.db import db


def process_token(pid: int) -> str | None:
    if sys.platform == "darwin":
        # в macOS нет /proc: время запуска процесса вместе с pid отличает живого владельца от повторно занятого pid
        try:
            result = subprocess.run(
                ["ps", "-o", "lstart=", "-p", str(int(pid))],
                capture_output=True, text=True, timeout=5, check=False,
                env={**os.environ, "LC_ALL": "C"},
            )
        except (OSError, subprocess.SubprocessError, ValueError):
            return None
        started = result.stdout.strip()
        return f"ps:{started}" if result.returncode == 0 and started else None
    if os.name != "nt":
        try:
            from pathlib import Path

            return "proc:" + Path(f"/proc/{pid}/stat").read_text().rsplit(") ", 1)[1].split()[19]
        except (OSError, IndexError):
            return None
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
    kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.OpenProcess(0x1000, False, pid)
    if not handle:
        if ctypes.get_last_error() == 5:
            return "access-denied"
        return None
    try:
        exit_code = wintypes.DWORD()
        if not kernel.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
            return "access-denied"
        if exit_code.value != 259:
            return None
        times = [wintypes.FILETIME() for _ in range(4)]
        if not kernel.GetProcessTimes(handle, *(ctypes.byref(value) for value in times)):
            return "access-denied"
        created = times[0].dwLowDateTime | (times[0].dwHighDateTime << 32)
        return f"win:{created}"
    finally:
        kernel.CloseHandle(handle)


def current_owner() -> tuple[int, str | None]:
    return os.getpid(), process_token(os.getpid())


def owner_alive(pid: int | None, token: str | None) -> bool:
    if not pid:
        return False
    actual = process_token(pid)
    return actual == "access-denied" or (actual is not None and actual == token)


def recover_interrupted_runs(is_active) -> list[int]:
    """Помечает брошенные ходы как прерванные; опасные операции заново не гоняем."""
    recovered = []
    reason = "Приложение было закрыто во время выполнения. История и изменения сохранены; задачу можно продолжить новым сообщением."
    with db() as conn:
        turns = conn.execute(
            "SELECT id,chat_id,owner_pid,owner_token FROM turns WHERE status='running'"
        ).fetchall()
        for turn in turns:
            if is_active(turn["chat_id"]) or owner_alive(turn["owner_pid"], turn["owner_token"]):
                continue
            conn.execute(
                "UPDATE turns SET status='failed',summary=?,completed_at=CURRENT_TIMESTAMP WHERE id=?",
                (reason, turn["id"]),
            )
            recovered.append(turn["id"])
            rows = conn.execute(
                "SELECT id,metadata_json FROM messages WHERE chat_id=? AND role='assistant'",
                (turn["chat_id"],),
            ).fetchall()
            for row in rows:
                try:
                    metadata = json.loads(row["metadata_json"] or "{}")
                except ValueError:
                    continue
                if not isinstance(metadata, dict) or metadata.get("status") != "running":
                    continue
                metadata.update(status="failed", interrupted=True, error=reason)
                conn.execute(
                    "UPDATE messages SET metadata_json=? WHERE id=?",
                    (json.dumps(metadata, ensure_ascii=False), row["id"]),
                )
            conn.execute(
                "UPDATE tasks SET status='failed',updated_at=CURRENT_TIMESTAMP WHERE chat_id=? AND status='running'",
                (turn["chat_id"],),
            )
    return recovered
