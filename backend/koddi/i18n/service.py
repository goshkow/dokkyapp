from __future__ import annotations

import json

from koddi.config import app_dir


def check_locales() -> dict:
    root = app_dir() / "locales"
    locale_files = {path.stem: path for path in root.glob("*.json")}
    errors: list[str] = []
    data: dict[str, dict[str, str]] = {}
    for locale, path in locale_files.items():
        try:
            parsed = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(parsed, dict):
                raise ValueError("top-level value must be an object")
            data[locale] = {str(key): str(value) for key, value in parsed.items()}
        except Exception as exc:
            errors.append(f"{path.name}: invalid UTF-8 JSON ({exc})")
    ru = set(data.get("ru", {}))
    en = set(data.get("en", {}))
    missing_ru = sorted(en - ru)
    missing_en = sorted(ru - en)
    mojibake = []
    for locale, values in data.items():
        for key, value in values.items():
            if "Р" in value and any(marker in value for marker in ("Рџ", "РЎ", "Рђ", "Рќ")):
                mojibake.append(f"{locale}.{key}")
    return {
        "ok": not errors and not missing_ru and not missing_en and not mojibake,
        "locales": sorted(data),
        "missing_ru": missing_ru,
        "missing_en": missing_en,
        "mojibake": mojibake,
        "errors": errors,
    }
