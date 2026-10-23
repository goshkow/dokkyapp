import os

import pytest

from koddi.desktop import configure_webengine_rendering

KEYS = ("KODDI_RENDERER", "KODDI_WEBENGINE_GPU", "QTWEBENGINE_CHROMIUM_FLAGS", "QSG_RHI_BACKEND", "QSG_RHI_PREFER_SOFTWARE_RENDERER", "QT_QUICK_BACKEND")


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for key in KEYS:
        monkeypatch.delenv(key, raising=False)
    yield
    # функция меняет os.environ напрямую: после теста переменные не должны утекать в другие
    for key in KEYS:
        os.environ.pop(key, None)


def flags() -> list[str]:
    return os.environ.get("QTWEBENGINE_CHROMIUM_FLAGS", "").split()


@pytest.mark.skipif(os.name != "nt", reason="режимы отрисовки Windows")
def test_gpu_mode_is_default_with_gpu_compositing():
    configure_webengine_rendering()
    # Chromium на ANGLE Direct3D 11 с композитингом на видеокарте, Qt Quick на OpenGL
    assert os.environ["QSG_RHI_BACKEND"] == "opengl"
    assert "--use-angle=d3d11" in flags()
    # композитинг процессором в одном потоке давал 50-60 мс на кадр: его в обычном режиме быть не должно
    assert "--disable-gpu-compositing" not in flags() and "--disable-gpu" not in flags()
    assert os.environ.get("QT_QUICK_BACKEND") != "software"


@pytest.mark.skipif(os.name != "nt", reason="режимы отрисовки Windows")
@pytest.mark.parametrize("value", ["0", "false", "off", "software"])
def test_software_mode_uses_warp_not_single_thread_compositing(monkeypatch, value):
    monkeypatch.setenv("KODDI_WEBENGINE_GPU", value)
    configure_webengine_rendering()
    # программный Direct3D 11 (WARP) и в Qt Quick, и в Chromium: многопоточно, видеокарта не нужна
    assert os.environ["QSG_RHI_BACKEND"] == "d3d11"
    assert os.environ["QSG_RHI_PREFER_SOFTWARE_RENDERER"] == "1"
    assert "--use-angle=d3d11-warp" in flags()
    assert "--disable-gpu-compositing" not in flags()
    assert os.environ.get("QT_QUICK_BACKEND") != "software"


@pytest.mark.skipif(os.name != "nt", reason="режимы отрисовки Windows")
def test_diagnostic_renderers(monkeypatch):
    monkeypatch.setenv("KODDI_RENDERER", "d3d11")
    configure_webengine_rendering()
    assert "QSG_RHI_BACKEND" not in os.environ and "--use-angle=d3d11" in flags()
    os.environ.pop("QTWEBENGINE_CHROMIUM_FLAGS", None)
    monkeypatch.setenv("KODDI_RENDERER", "opengl")
    configure_webengine_rendering()
    assert os.environ["QSG_RHI_BACKEND"] == "opengl" and "--use-angle=gl" in flags()


def test_safe_renderer_is_cpu_only(monkeypatch):
    monkeypatch.setenv("KODDI_RENDERER", "safe")
    configure_webengine_rendering()
    assert os.environ["QT_QUICK_BACKEND"] == "software"
    assert "--disable-gpu" in flags() and "--disable-gpu-compositing" in flags()
    assert "QSG_RHI_BACKEND" not in os.environ


def test_custom_flags_are_kept(monkeypatch):
    monkeypatch.setenv("QTWEBENGINE_CHROMIUM_FLAGS", "--custom-flag")
    configure_webengine_rendering()
    assert os.environ["QTWEBENGINE_CHROMIUM_FLAGS"].startswith("--custom-flag")


@pytest.mark.skipif(os.name != "nt", reason="режимы отрисовки Windows")
def test_user_renderer_flags_win(monkeypatch):
    monkeypatch.setenv("QTWEBENGINE_CHROMIUM_FLAGS", "--use-angle=vulkan")
    configure_webengine_rendering()
    assert flags() == ["--use-angle=vulkan"]
