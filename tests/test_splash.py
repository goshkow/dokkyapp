import json
import os
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPointF, Qt  # noqa: E402
from PySide6.QtGui import QImage, QMouseEvent  # noqa: E402
from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

from koddi.desktop import splash  # noqa: E402
from koddi.desktop.web_host import WindowsFrame  # noqa: E402


def test_load_defaults_when_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("KODDI_DATA_DIR", str(tmp_path))
    assert splash.load() == splash.DEFAULT


def test_save_and_load_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(splash, "_file", lambda: tmp_path / "startup.json")
    splash.save("light", True, (1.0, 0.5, 0.25))
    assert splash.load() == {"theme": "light", "glass": True, "color": [1.0, 0.5, 0.25]}


def test_load_ignores_broken_file(tmp_path, monkeypatch):
    path = tmp_path / "startup.json"
    monkeypatch.setattr(splash, "_file", lambda: path)
    path.write_text("{oops", encoding="utf-8")
    assert splash.load() == splash.DEFAULT
    path.write_text(json.dumps({"theme": "neon", "glass": True, "color": [0, 0, 0]}), encoding="utf-8")
    assert splash.load() == splash.DEFAULT


def test_save_rejects_bad_theme(tmp_path, monkeypatch):
    path = tmp_path / "startup.json"
    monkeypatch.setattr(splash, "_file", lambda: path)
    splash.save("neon", True, (0, 0, 0))
    assert not path.exists()


def test_mark_exists():
    assert splash.mark_path().is_file()


def test_glass_choice_is_kept_only_after_switching(tmp_path, monkeypatch):
    path = tmp_path / "startup.json"
    monkeypatch.setattr(splash, "_file", lambda: path)
    # чистая установка: файла нет, и выбора ещё не было
    assert splash.glass_turned_off() is None
    # запись темы до включения стекла не считается выбором
    splash.save("dark", False, (0, 0, 0))
    assert splash.glass_turned_off() is None
    # переключили: запись темы выбор не сбрасывает, включение тоже запоминается
    splash.save("dark", False, (0, 0, 0), glass_off=True)
    splash.save("light", False, (1, 1, 1))
    assert splash.glass_turned_off() is True
    splash.save("light", True, (1, 1, 1), glass_off=False)
    splash.save("dark", True, (0, 0, 0))
    assert splash.glass_turned_off() is False
    path.write_text("[]", encoding="utf-8")
    assert splash.glass_turned_off() is None
    path.write_text(json.dumps({"glass_off": "yes"}), encoding="utf-8")
    assert splash.glass_turned_off() is None


@pytest.fixture
def screen():
    app = QApplication.instance() or QApplication([])
    yield app
    app.processEvents()


def make_splash(theme="dark", window_action=None):
    parent = QWidget()
    color = [0.08, 0.08, 0.08] if theme == "dark" else [1.0, 1.0, 1.0]
    widget = splash.Splash(parent, {"theme": theme, "glass": False, "color": color}, window_action=window_action)
    widget.animation.stop()
    widget.level = 1.0
    widget.resize(800, 600)
    return parent, widget


def mark_pixels(widget):
    image = widget.grab().toImage()
    left, top = (widget.width() - splash.MARK) // 2, (widget.height() - splash.MARK) // 2
    return [image.pixelColor(x, y).red() for y in range(top, top + splash.MARK) for x in range(left, left + splash.MARK)]


@pytest.mark.parametrize("theme", ["dark", "light"])
def test_mark_edges_are_smooth(screen, theme):
    _parent, widget = make_splash(theme)
    levels = set(mark_pixels(widget))
    # без сглаживания знак был бы почти из двух цветов
    assert len(levels) > 20


def test_light_mark_has_no_halo(screen):
    _parent, widget = make_splash("light")
    image = widget._mark().toImage().convertToFormat(QImage.Format.Format_ARGB32)
    # белый знак стал чёрным целиком; переворот в premultiplied дал бы светлые края
    edges = [image.pixelColor(x, y) for y in range(image.height()) for x in range(image.width())]
    assert any(20 < color.alpha() < 235 for color in edges)
    assert all(color.red() < 40 for color in edges if color.alpha() > 32)


def press(widget, kind, x, y):
    point = QPointF(x, y)
    event = QMouseEvent(kind, point, point, Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(widget, event)


@pytest.mark.parametrize(("x", "action"), [(670, "minimize"), (710, "maximize"), (760, "close")])
def test_caption_buttons_run_window_actions(screen, x, action):
    done = []
    _parent, widget = make_splash(window_action=done.append)
    press(widget, QMouseEvent.Type.MouseButtonPress, x, 20)
    press(widget, QMouseEvent.Type.MouseButtonRelease, x, 20)
    assert done == [action]


def test_caption_button_needs_release_on_itself(screen):
    done = []
    _parent, widget = make_splash(window_action=done.append)
    press(widget, QMouseEvent.Type.MouseButtonPress, 760, 20)
    press(widget, QMouseEvent.Type.MouseButtonRelease, 400, 300)
    assert done == []


@pytest.mark.parametrize(("x", "y"), [(661, 20), (662, 20), (707, 41), (708, 20), (799, 41), (799, 42), (760, 0)])
def test_caption_buttons_match_window_hit_test(screen, x, y):
    _parent, widget = make_splash(window_action=lambda _action: None)
    # геометрия страницы после загрузки (WM_NCHITTEST), коды как в run_caption_action; край для перетаскивания у окна на весь экран равен нулю
    code = WindowsFrame.caption_hit_test(x, y, widget.width(), 1, maximized=True)
    assert widget._caption_action(x, y) == {None: "", 8: "minimize", 9: "maximize", 20: "close"}[code]


def test_mark_follows_fractional_screen_scale(screen, monkeypatch):
    _parent, widget = make_splash()
    monkeypatch.setattr(widget, "devicePixelRatioF", lambda: 1.25)
    mark = widget._mark()
    assert (mark.width(), mark.devicePixelRatio()) == (80, 1.25)
    monkeypatch.setattr(widget, "devicePixelRatioF", lambda: 2.0)
    assert widget._mark().width() == 128


def test_caption_hover_paints_close_button(screen):
    _parent, widget = make_splash(window_action=lambda _action: None)
    before = widget.grab().toImage().pixelColor(790, 4).name()
    widget._set_hovered("close")
    assert widget.grab().toImage().pixelColor(790, 4).name() == "#c42b1c"
    widget._set_hovered("")
    assert widget.grab().toImage().pixelColor(790, 4).name() == before


def test_caption_double_click_maximizes_and_single_moves(screen, monkeypatch):
    done, moved = [], []
    parent, widget = make_splash(window_action=done.append)
    parent.show()
    handle = parent.windowHandle()
    monkeypatch.setattr(widget, "window", lambda: SimpleNamespace(windowHandle=lambda: SimpleNamespace(startSystemMove=lambda: moved.append(handle))))
    press(widget, QMouseEvent.Type.MouseButtonPress, 300, 20)
    assert (done, len(moved)) == ([], 1)
    press(widget, QMouseEvent.Type.MouseButtonPress, 300, 20)
    assert (done, len(moved)) == (["maximize"], 1)
    # ниже шапки окно не тянется
    press(widget, QMouseEvent.Type.MouseButtonPress, 300, 200)
    assert (done, len(moved)) == (["maximize"], 1)


def test_no_caption_buttons_without_window_action(screen):
    _parent, widget = make_splash()
    assert widget._caption_action(760, 20) == ""
    press(widget, QMouseEvent.Type.MouseButtonPress, 760, 20)
    press(widget, QMouseEvent.Type.MouseButtonRelease, 760, 20)
    assert not widget.hasMouseTracking()
