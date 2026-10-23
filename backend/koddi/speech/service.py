from __future__ import annotations

from pathlib import Path
import importlib.util
import os

from koddi.storage.db import get_setting, set_setting


def consent_status() -> dict:
    return {
        "consented": bool(get_setting("speech_whisper_consent", False)),
        "model": get_setting("speech_whisper_model", "tiny"),
        "model_status": whisper_model_status().get("status"),
    }


def set_consent(consented: bool, model: str = "tiny") -> dict:
    set_setting("speech_whisper_consent", consented)
    set_setting("speech_whisper_model", model)
    return consent_status()


def whisper_model_status() -> dict:
    model = get_setting("speech_whisper_model", "tiny")
    # whisper по умолчанию качает веса сюда; папка данных Koddi не значит, что модель готова
    cache_root = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "whisper"
    filenames = {"tiny": "tiny.pt", "base": "base.pt", "small": "small.pt"}
    path = cache_root / filenames.get(model, f"{model}.pt")
    runtime_available = importlib.util.find_spec("whisper") is not None
    return {
        "model": model,
        "path": str(path),
        "runtime_available": runtime_available,
        "status": "installed" if runtime_available and path.exists() else "not_installed",
    }


def transcribe_audio(path: str) -> dict:
    if not get_setting("speech_whisper_consent", False):
        return {"ok": False, "needs_consent": True, "text": ""}
    audio = Path(path)
    if not audio.exists():
        return {"ok": False, "error": "audio file not found", "text": ""}
    status = whisper_model_status()
    if not status["runtime_available"]:
        return {
            "ok": False,
            "engine": "unavailable",
            "error": "Whisper runtime is not installed",
            "text": "",
            "message": "Установите дополнительный runtime: pip install 'koddi[speech]'.",
        }
    try:
        import whisper  # type: ignore

        model_name = get_setting("speech_whisper_model", "tiny")
        model = whisper.load_model(model_name)
        result = model.transcribe(str(audio))
        return {"ok": True, "text": result.get("text", ""), "engine": "openai-whisper"}
    except Exception as exc:
        return {
            "ok": False,
            "engine": "unavailable",
            "error": str(exc),
            "text": "",
            "message": "Не удалось выполнить локальную расшифровку Whisper.",
        }
