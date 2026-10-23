"""Запускает релизную сборку под Windows с жёстким лимитом CPU через Job Object."""

from __future__ import annotations

import argparse
import ctypes
import os
import shutil
import subprocess
from ctypes import wintypes
from pathlib import Path


class CpuRate(ctypes.Structure):
    _fields_ = [("ControlFlags", wintypes.DWORD), ("CpuRate", wintypes.DWORD)]


def main() -> None:
    parser = argparse.ArgumentParser(description="Сборка Кодди с ограничением CPU")
    parser.add_argument("--cpu-percent", type=int, default=75)
    parser.add_argument("--output", default="dist_koddi_test")
    parser.add_argument("--compiler", choices=["msvc", "gcc"], default="msvc")
    args = parser.parse_args()
    if os.name != "nt" or not 10 <= args.cpu_percent <= 80:
        parser.error("Нужна Windows и ограничение CPU от 10 до 80 процентов")
    root = Path(__file__).resolve().parents[1]
    output = (root / args.output).resolve()
    if output == root or not output.is_relative_to(root):
        parser.error("Папка сборки должна находиться внутри проекта")
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    kernel.CreateJobObjectW.restype = wintypes.HANDLE
    kernel.SetInformationJobObject.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
    ]
    kernel.SetInformationJobObject.restype = wintypes.BOOL
    kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel.AssignProcessToJobObject.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.GetActiveProcessorCount.argtypes = [wintypes.WORD]
    kernel.GetActiveProcessorCount.restype = wintypes.DWORD
    job = kernel.CreateJobObjectW(None, None)
    if not job:
        raise ctypes.WinError(ctypes.get_last_error())
    process = None
    try:
        limits = CpuRate(0x1 | 0x4, args.cpu_percent * 100)
        if not kernel.SetInformationJobObject(job, 15, ctypes.byref(limits), ctypes.sizeof(limits)):
            raise ctypes.WinError(ctypes.get_last_error())
        logical_cpus = kernel.GetActiveProcessorCount(0xFFFF)
        jobs = max(1, int(logical_cpus * args.cpu_percent / 100))
        print(f"Сборка: CPU не более {args.cpu_percent}%, компиляторов {jobs}", flush=True)
        process = subprocess.Popen(
            [
                shutil.which("pwsh") or "powershell",
                "-NoProfile",
                "-File",
                str(root / "scripts/build_budget_worker.ps1"),
                "-OutputDir",
                args.output,
                "-Compiler",
                args.compiler,
                "-Jobs",
                str(jobs),
            ],
            cwd=root,
            stdin=subprocess.PIPE,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        # воркер ждёт stdin, пока дочерние процессы не попадут под лимит
        if not kernel.AssignProcessToJobObject(job, wintypes.HANDLE(int(process._handle))):
            raise ctypes.WinError(ctypes.get_last_error())
        process.stdin.write(b"start\n")
        process.stdin.close()
        raise SystemExit(process.wait())
    except BaseException:
        if process is not None and process.poll() is None:
            kernel.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
            kernel.TerminateJobObject(job, 1)
            process.terminate()
            process.wait()
        raise
    finally:
        kernel.CloseHandle(job)


if __name__ == "__main__":
    main()
