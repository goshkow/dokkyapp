from __future__ import annotations

import os
import hashlib
import platform
import sqlite3
import sys

from koddi.config import data_dir, is_packaged_build
from koddi.remote import remote_config


BUILD_NUMBER = "20261016.2"


async def diagnostics() -> dict:
    mode = "override" if os.getenv("KODDI_DATA_DIR") else (
        "portable" if os.getenv("KODDI_PORTABLE") == "1" else "user"
    )
    profile = {"mode": mode, "available": False, "database_present": False}
    ok = True
    try:
        directory = data_dir()
        profile["available"] = directory.is_dir()
        profile["database_present"] = (directory / "koddi.sqlite3").is_file()
    except OSError:
        ok = False
    try:
        ai_configured = bool(remote_config().get("base_url"))
    except (OSError, sqlite3.Error, ValueError, TypeError):
        ai_configured = None
        ok = False
    return {
        "ok": ok,
        "build_number": BUILD_NUMBER,
        "packaged": is_packaged_build(),
        "python": platform.python_version(),
        "platform": platform.platform(),
        # поля оставлены для совместимости, абсолютные пути профиля не раскрываем.
        "data_dir": "<profile>",
        "project_root": "<application>",
        "profile": profile,
        'profile_id':hashlib.sha256(os.path.normcase(str(data_dir().absolute())).encode()).hexdigest()[:12] if profile['available'] else None,
        "ai_configured": ai_configured,
        "caption_rendering": "macos-native" if sys.platform == "darwin" else "windows-caption-glyph",
        "native_dwm_caption": False,
        "external_gates": (
            ["macos_traffic_lights_and_fullscreen"]
            if sys.platform == "darwin" else ["windows_10_11_caption_and_snap"]
        ),
    }


def update_check() -> dict:
    return {
        "ok": True,
        "status": "disabled",
        "reason": "unconfigured",
        "external_gates": ["pinned_update_key", "release_https_url", "publisher_certificate"],
    }
