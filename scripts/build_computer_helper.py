"""Сборка собственного помощника macOS; системные разрешения не запрашиваются."""

from __future__ import annotations

import argparse
import os
import platform
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def build(output: Path) -> None:
    if sys.platform != "darwin":
        raise RuntimeError("Swift-помощник собирается на macOS")
    source = ROOT / "backend/koddi/computer_use/native/mac.swift"
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(output.name + ".building")
    try:
        target = platform.machine() + "-apple-macosx14.0"
        subprocess.run(
            [
                "xcrun",
                "swiftc",
                "-parse-as-library",
                "-O",
                "-target",
                target,
                "-framework",
                "AppKit",
                "-framework",
                "ApplicationServices",
                "-framework",
                "ScreenCaptureKit",
                str(source),
                "-o",
                str(temporary),
            ],
            check=True,
        )
        os.replace(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "backend/koddi/computer_use/native/dokky-computer-helper",
    )
    build(parser.parse_args().output)
