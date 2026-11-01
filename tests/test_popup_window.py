"""Окно меню: положение у кнопки и разбор запросов страницы."""

from __future__ import annotations

from types import SimpleNamespace

import json

import pytest

pytest.importorskip("PySide6")

from koddi.desktop import popup_window  # noqa: E402
from koddi.desktop.popup_window import GAP, place  # noqa: E402

SCREEN = (0, 0, 1440, 900)


def test_opens_right_above_button_and_may_cover_field():
    # кнопка режима под полем ввода: меню вплотную над кнопкой, а не над полем
    x, y = place((1000, 840, 120, 30), (200, 150), "above", "start", SCREEN)
    assert (x, y) == (1000, 840 - 150 - GAP)


def test_opens_below_when_asked_and_flips_when_no_room():
    assert place((100, 100, 80, 30), (200, 150), "below", "start", SCREEN)[1] == 130 + GAP
    assert place((100, 800, 80, 30), (200, 150), "below", "start", SCREEN)[1] == 800 - 150 - GAP
    assert place((100, 20, 80, 30), (200, 150), "above", "start", SCREEN)[1] == 50 + GAP


def test_align_end_and_screen_edges():
    assert place((300, 100, 80, 30), (200, 150), "below", "end", SCREEN)[0] == 180
    assert place((1400, 100, 80, 30), (200, 150), "below", "start", SCREEN)[0] == 1440 - 200 - 8
    # меню шире места слева от кнопки не уходит за экран
    assert place((10, 100, 20, 20), (200, 150), "below", "end", SCREEN)[0] == 8


def test_context_menu_opens_from_point_and_can_leave_app_window():
    assert place((500, 300, 0, 0), (180, 200), "below", "start", SCREEN) == (500, 300)
    assert place((500, 850, 0, 0), (180, 200), "below", "start", SCREEN) == (500, 650)


def test_second_screen_uses_its_own_bounds():
    assert place((2000, 500, 100, 30), (200, 150), "below", "start", (1440, 0, 1920, 1080)) == (2000, 530 + GAP)


@pytest.mark.parametrize("payload", ["", "[]", "{}", json.dumps({"id": "x"}), json.dumps({"id": 1, "x": 1e9, "y": 0, "w": 1, "h": 1, "vw": 10, "vh": 10}),
                                     json.dumps({"id": 1, "x": 1, "y": 0, "w": 1, "h": 1, "vw": 0, "vh": 10})])
def test_bad_anchor_is_ignored(payload):
    assert popup_window.parse_anchor(payload) is None


@pytest.mark.parametrize("payload", ["", "{}", json.dumps({"id": 1, "w": 0, "h": 5}), json.dumps({"id": 1, "w": 5, "h": 99999})])
def test_bad_size_is_ignored(payload):
    assert popup_window.parse_size(payload) is None


def test_state_snapshots_do_not_reach_the_menu_window():
    from koddi.desktop.web_host import WebHost

    sent: dict[str, list[str]] = {"main": [], "popup": []}

    def view(role):
        page = SimpleNamespace(runJavaScript=sent[role].append)
        return SimpleNamespace(page=lambda: page)

    host = SimpleNamespace(view=view("main"), tiles={}, popup=SimpleNamespace(view=view("popup")))
    WebHost.relay(host, "composer", '{"k":"state","n":"ui","s":{}}')
    assert len(sent["main"]) == 1 and sent["popup"] == []
    WebHost.relay(host, "composer", '{"k":"call","id":1,"to":"popup","from":"composer","f":"popupShow","a":[]}')
    assert len(sent["main"]) == 2 and len(sent["popup"]) == 1
