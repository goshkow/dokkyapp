"""Список выбора у поля ввода на стекле: запрос страницы к системному меню и ответ ей."""

import json

from koddi.desktop import popup_menu


def request(**changes):
    data = {
        "id": 3, "x": 120, "y": 640, "vw": 1200, "vh": 800, "selected": "full",
        "items": [{"value": "ask", "label": "Вручную"}, {"value": "full", "label": "Разрешить всё", "disabled": False}],
    }
    data.update(changes)
    return json.dumps(data)


def test_valid_request_is_normalized():
    parsed = popup_menu.parse(request())
    assert parsed["id"] == 3 and parsed["selected"] == "full"
    assert parsed["items"][1] == {"value": "full", "label": "Разрешить всё", "disabled": False}


def test_bad_requests_are_dropped():
    assert popup_menu.parse("не json") is None
    assert popup_menu.parse(request(id="3")) is None
    assert popup_menu.parse(request(x=5000)) is None
    assert popup_menu.parse(request(items=[])) is None
    assert popup_menu.parse(request(items=[{"value": "a", "label": "  "}])) is None
    assert popup_menu.parse(request(items=[{"value": "a", "label": "x" * 500}])) is None
    assert popup_menu.parse(request(items=[{"value": str(i), "label": str(i)} for i in range(40)])) is None
    assert popup_menu.parse(request(selected=5))["selected"] is None


def test_result_script_escapes_value():
    assert popup_menu.result_script(3, "full") == 'window.koddiPopupResult?.(3, "full")'
    assert popup_menu.result_script(3, None) == "window.koddiPopupResult?.(3, null)"
    assert popup_menu.result_script(3, '");alert(1)').endswith('"\\");alert(1)")')
