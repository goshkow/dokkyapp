"""Настоящее окно Mac: медленный старт API, светофор и стекло при анимациях панелей, ресайзе и полном экране.

Без аккаунта и платных запросов. Запускать из графической сессии (Терминал), не из песочницы.
Во время каждой анимации каждые 8 мс сверяются светофор, размер стекла и отступ страницы; по кадрам страницы
считается самый длинный разрыв между кадрами (рывок).
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time

os.environ["KODDI_DATA_DIR"] = tempfile.mkdtemp(prefix="dokky-macos-smoke-")
os.environ["KODDI_INTERNAL_TEST"] = "1"

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from koddi.desktop import macos_frame, macos_menu, web_host
from koddi.desktop.transport import desktop_access

# светофор ставит и держит сам AppKit (NSToolbar): его положение после загрузки - эталон на всё время теста
# самый длинный разрыв между кадрами страницы во время анимации, мс
MAX_FRAME_GAP = 150

RECORD = """
window.__gaps = []; window.__last = performance.now(); window.__run = true;
(function tick(now) { window.__gaps.push(now - window.__last); window.__last = now; if (window.__run) requestAnimationFrame(tick); })(performance.now());
true
"""
STOP = "window.__run = false; JSON.stringify(window.__gaps)"


def main():
    if sys.platform != "darwin":
        raise SystemExit("Нужна macOS")
    macos_menu.set_process_name()
    app = QApplication([])
    app.setApplicationName("Докки")
    port = web_host._available_port()
    backend = web_host.DesktopBackend(port)
    init = backend.server.config.app
    # задержка до uvicorn.startup воспроизводит запуск с медленным профилем
    migrate = web_host.desktop_server.migrate_legacy_data

    def delayed():
        time.sleep(1.5)
        migrate()

    web_host.desktop_server.migrate_legacy_data = delayed
    # запуск как у обычного пользователя: окно со стеклом поднимается ещё до загрузки страницы
    from koddi.desktop import splash

    splash.save("dark", True, (0.078, 0.078, 0.078))
    host = web_host.WebHost(port, desktop_access.key, backend)
    host.resize(1300, 800)
    backend.start()  # как в main: сервер стартует до показа окна
    host.show()
    result = {"errors": [], "slow_start": False, "violations": [], "scenarios": {}, "lights_samples": 0, "lights_bad": 0}

    # светофор проверяем по тому, что реально ушло на экран: наблюдатель стоит после фиксации кадра Core Animation (порядок 2000000)
    from CoreFoundation import CFRunLoopAddObserver, CFRunLoopGetMain, CFRunLoopObserverCreateWithHandler, kCFRunLoopBeforeWaiting, kCFRunLoopCommonModes

    reference = {}

    def committed_lights(_observer, _activity):
        if "geometry" not in reference or host.isFullScreen():
            return
        geometry = macos_frame.lights_geometry(host)
        if geometry is None:
            return
        result["lights_samples"] += 1
        want = reference["geometry"]
        if any(abs(geometry[name] - want[name]) > 0.5 for name in ("left", "right", "center")):
            # AppKit перекладывает заголовок после фиксации кадра, а правка идёт перед следующей: на экран это не попадает
            result["lights_bad"] += 1
            result.setdefault("lights_bad_examples", [])
            if len(result["lights_bad_examples"]) < 3:
                result["lights_bad_examples"].append(geometry)

    watcher = CFRunLoopObserverCreateWithHandler(None, kCFRunLoopBeforeWaiting, True, 2100000, committed_lights)
    CFRunLoopAddObserver(CFRunLoopGetMain(), watcher, kCFRunLoopCommonModes)
    deadline = time.monotonic() + 30

    def guarded(fn):
        def run(*args):
            try:
                fn(*args)
            except Exception as exc:  # noqa: BLE001
                result["errors"].append(repr(exc))
                app.quit()
        return run

    def check_frame(label):
        """Светофор, стекло и страница в одном кадре окна: нарушения копятся в результате."""
        window = macos_frame._window(host)
        bad = []
        glass = host.glass.background if host.glass else None
        content = window.contentView()
        if glass is not None and not glass.isHidden():
            g, c = glass.frame(), content.bounds()
            if (g.origin.x, g.origin.y, g.size.width, g.size.height) != (c.origin.x, c.origin.y, c.size.width, c.size.height):
                bad.append(f"стекло {g.size.width}x{g.size.height} при окне {c.size.width}x{c.size.height}")
        for role, tile in host.tiles.items():
            if role == "composer":
                continue
            if tile.glass is not None and tile.open and not tile.glass.isHidden():
                frame = tile.glass.frame()
                if (frame.origin.y, frame.size.height) != (8.0, content.bounds().size.height - 16.0):
                    bad.append(f"плитка {role} {frame.origin.y}+{frame.size.height} при окне {content.bounds().size.height}")
        if host.view.mapTo(host, host.view.rect().topLeft()).y() != 0:
            bad.append("страница сдвинута вниз")
        if host.view.height() != host.height():
            bad.append(f"страница {host.view.height()} при окне {host.height()}")
        if bad:
            result["violations"].append(f"{label}: " + "; ".join(bad))

    def sample_for(ms, label, then, step=8):
        started = time.monotonic()

        def tick():
            try:
                check_frame(label)
            except Exception as exc:  # noqa: BLE001
                result["errors"].append(repr(exc))
                app.quit()
                return
            if (time.monotonic() - started) * 1000 < ms:
                QTimer.singleShot(step, tick)
            else:
                then()

        tick()

    def check_composer(label, then):
        """Нативное стекло под полем ввода совпадает с его рамкой на странице."""
        def got(raw):
            box = json.loads(raw) if raw else None
            tile = host.tiles.get("composer")
            view = tile.regions.views.get("composer") if tile else None
            if box is None or view is None:
                result["violations"].append(f"{label}: стекла под полем ввода нет")
            else:
                frame = view.frame()
                flipped = bool(macos_frame._window(host).contentView().isFlipped())
                height = macos_frame._window(host).contentView().bounds().size.height
                y = frame.origin.y if flipped else height - frame.origin.y - frame.size.height
                got_box = (frame.origin.x, y, frame.size.width, frame.size.height)
                want = (box["x"], box["y"], box["w"], box["h"])
                if any(abs(a - b) > 1.5 for a, b in zip(got_box, want)):
                    result["violations"].append(f"{label}: стекло поля ввода {got_box} при рамке {want}")
            then()

        host.view.page().runJavaScript("(() => { const r = document.querySelector('.composer-field')?.getBoundingClientRect(); return r ? JSON.stringify({x: r.left, y: r.top, w: r.width, h: r.height}) : ''; })()", guarded(got))

    def animate(name, action, then):
        """Запускает действие страницы, пишет кадры страницы и сверяет окно каждые 8 мс."""
        def started(_):
            host.menu_action(action)

            def finish():
                host.view.page().runJavaScript(STOP, guarded(collect))

            def collect(raw):
                gaps = json.loads(raw)
                worst = round(max(gaps), 1) if gaps else 0
                result["scenarios"][name] = {"frames": len(gaps), "worst_gap_ms": worst}
                if worst > MAX_FRAME_GAP:
                    result["violations"].append(f"{name}: рывок {worst} мс")
                QTimer.singleShot(250, guarded(lambda: check_composer(name, then)))

            sample_for(900, name, finish)

        host.view.page().runJavaScript(RECORD, guarded(started))

    @guarded
    def slow_start():
        assert not backend.server.started
        assert host._load_attempts == 0
        assert host.startup_label.isVisible() and host.view.isVisible() and host.view.width() > 400
        result["slow_start"] = True

    @guarded
    def ready():
        if time.monotonic() > deadline:
            raise RuntimeError("Не загрузилось окно Mac")
        host.view.page().runJavaScript("Boolean(document.querySelector('#toggle-left') && window.koddiMenu)", guarded(loaded))

    @guarded
    def loaded(ok):
        if not ok or not host._ui_loaded:
            QTimer.singleShot(100, ready)
            return
        assert host.glass is not None and host.glass.enabled and host.glass.background is not None
        assert host.isVisible() and host.width() > 400 and host.height() > 300, str(host.size())
        check_frame("после загрузки")
        reference["geometry"] = macos_frame.lights_geometry(host)
        result["lights_reference"] = reference["geometry"]
        # поле ввода по умолчанию в одну строку: скрытый при старте вид давал нулевую ширину, и поле залипало высоким
        host.view.page().runJavaScript("document.querySelector('.composer-field').getBoundingClientRect().height", guarded(composer_height))

    def composer_height(value):
        assert value is not None and value < 90, f"поле ввода {value}"
        result["composer_height"] = value
        QTimer.singleShot(800, run_steps)

    def alignment():
        """Верхняя строка страницы выровнена по светофору: центры кнопок на одной высоте, отступы по краям равны."""
        want = reference["geometry"]
        code = """JSON.stringify({vars: getComputedStyle(document.documentElement).getPropertyValue('--lights-center'),
          nav: (() => { const r = document.querySelector('#toggle-left')?.getBoundingClientRect(); return r ? r.top + r.height / 2 : null; })(),
          right: (() => { const r = document.querySelector('#toggle-right')?.getBoundingClientRect(); return r && r.width ? [r.top + r.height / 2, innerWidth - r.right] : null; })()})"""

        def got(raw):
            info = json.loads(raw)
            assert abs(float(info["vars"].replace("px", "")) - want["center"]) < 0.6, f"--lights-center {info['vars']} при светофоре {want}"
            if info["nav"] is not None:
                assert abs(info["nav"] - want["center"]) < 1.5, f"кнопки навигации {info['nav']} при светофоре {want['center']}"
            result["alignment"] = info
            run_steps()

        host.view.page().runJavaScript(code, guarded(got))


    steps = []
    steps.append(lambda: alignment())

    def modal_over_tiles():
        """Пока открыто модальное окно, плитки лежат под страницей, после закрытия - снова над ней."""
        def order():
            content = macos_frame._window(host).contentView()
            subviews = list(content.subviews())
            anchor = macos_frame.anchor_in(content, host.glass._objc(host.view))
            web = [i for i, v in enumerate(subviews) if v.isEqual_(anchor)][0]
            return {role: subviews.index(tile.regions.views["composer"] if role == "composer" else tile.glass) > web for role, tile in host.tiles.items()}

        assert all(order().values()), f"до окна плитки не над страницей {order()}"
        host.menu_action("settings")

        def opened():
            state = order()
            assert not any(state.values()), f"плитки закрывают модальное окно {state}"
            host.view.page().runJavaScript("document.querySelector('dialog[open]')?.close(); true")
            QTimer.singleShot(700, guarded(closed))

        def closed():
            state = order()
            assert all(state.values()), f"после закрытия плитки не вернулись над страницей {state}"
            result["modal_order"] = True
            run_steps()

        QTimer.singleShot(900, guarded(opened))

    steps.append(modal_over_tiles)

    def menu_preserves_composer():
        """меню остаётся выше поля и не меняет порядок нативного стекла"""
        content = macos_frame._window(host).contentView()
        tile = host.tiles["composer"]
        native = tile.regions.views["composer"]
        before = list(content.subviews()).index(native)
        code = "document.querySelector('[aria-label=\"Разрешения действий\"]').click(); true"

        def opened():
            assert list(content.subviews()).index(native) == before, "меню переложило стекло"

            def measured(raw):
                boxes = json.loads(raw)
                assert boxes["menu"] <= boxes["field"] - 4, str(boxes)
                result["composer_menu"] = True
                host.view.page().runJavaScript("document.dispatchEvent(new KeyboardEvent('keydown', {key:'Escape', bubbles:true})); true")
                QTimer.singleShot(250, run_steps)

            host.view.page().runJavaScript("JSON.stringify({menu: document.querySelector('.popover').getBoundingClientRect().bottom, field: document.querySelector('.composer-field').getBoundingClientRect().top})", guarded(measured))

        host.view.page().runJavaScript(code, lambda _value: QTimer.singleShot(350, guarded(opened)))

    steps.append(menu_preserves_composer)

    def glass_tuner():
        """Окно настройки стекла открывается и меняет вид вживую, «Сбросить» возвращает стандарт."""
        from koddi.desktop import macos_glass

        host.open_glass_tuner()
        tuner = host._glass_tuner
        assert tuner.isVisible()
        tuner.theme.setCurrentIndex(0)
        tuner.base.set(55)
        tuner.alpha.set(20)
        tuner._changed()
        assert abs(macos_glass.SETTINGS["dark"]["base"] - 0.55) < 1e-9 and abs(macos_glass.SETTINGS["dark"]["tint"][3] - 0.2) < 1e-9
        tuner._reset()
        assert macos_glass.SETTINGS["dark"] == macos_glass.DEFAULTS["dark"]
        tuner.close()
        result["glass_tuner"] = True
        QTimer.singleShot(300, run_steps)

    steps.append(glass_tuner)

    def run_steps():
        if not steps:
            result["done"] = True
            app.quit()
            return
        steps.pop(0)()

    # нативная плитка левого меню: свой веб-вид внутри NSGlassEffectView
    def tile_js(code, then, label, role="sidebar"):
        host.tiles[role].view.page().runJavaScript(code, guarded(then))

    def wait_for(code, check, then, label, limit=8.0, role="sidebar"):
        started = time.monotonic()

        def poll():
            tile_js(code, got, label, role)

        def got(value):
            if check(value):
                then(value)
            elif time.monotonic() - started > limit:
                raise AssertionError(f"{label}: не дождались, последнее значение {value!r}")
            else:
                QTimer.singleShot(120, guarded(poll))

        poll()

    def pasted_text_roundtrip():
        """вставка в WK-плитке загружается, кнопка главного вида возвращает исходник в плитку"""
        source = "# Нативная вставка\n" + "Проверка текста.\n" * 500
        code = """(() => { const node = document.querySelector('#prompt');
          const text = SOURCE; const event = new Event('paste', { bubbles:true, cancelable:true });
          Object.defineProperty(event, 'clipboardData', { value:{ files:[], getData:() => text } });
          return !node.dispatchEvent(event); })()""".replace("SOURCE", json.dumps(source, ensure_ascii=False))
        started = time.monotonic()

        def poll():
            host.view.page().runJavaScript("Boolean(document.querySelector('.attachment-card:not(.is-upload) .attachment-as-text'))", guarded(uploaded))

        def uploaded(value):
            if not value:
                if time.monotonic() - started > 12:
                    raise AssertionError("вложение нативной вставки не загрузилось")
                QTimer.singleShot(100, guarded(poll))
                return
            tile_js("""JSON.stringify(['.composer-field','#prompt','#attach','#voice','#send'].map(selector => {
              const r=document.querySelector(selector).getBoundingClientRect(); return r.y+r.height/2;
            }))""", aligned, "выравнивание строки с вложением", role="composer")

        def aligned(raw):
            centers = json.loads(raw)
            assert max(centers) - min(centers) <= 1.5, f"строка с вложением съехала: {centers}"
            result["pasted_text_alignment"] = True
            host.view.page().runJavaScript("document.querySelector('.attachment-as-text').click(); true", guarded(restored))

        def restored(_):
            wait_for("document.querySelector('#prompt')?.value || ''", lambda value: value == source,
                     lambda _value: (result.update(pasted_text_roundtrip=True), run_steps()), "возврат текста в WK-плитку", role="composer")

        tile_js(code, lambda value: poll() if value else (_ for _ in ()).throw(AssertionError("вставка не перехвачена")), "вставка", role="composer")

    def tile_present():
        assert "sidebar" in host.tiles, "плитки нет"
        tile = host.tiles["sidebar"]
        frame = tile.glass.frame()
        assert (frame.origin.x, frame.origin.y, frame.size.width) == (8.0, 8.0, 272.0), str(frame)
        wait_for("document.querySelector('.brand-word')?.textContent || ''", lambda v: v == "Докки",
                 lambda v: (result.update(tile_content=v), run_steps()), "содержимое плитки")

    def tile_roundtrip():
        """Клик в плитке доходит до главного вида, а новое состояние возвращается в плитку."""
        badge = "document.querySelector('.mode-badge')?.textContent || ''"

        def first(before):
            result["badge_before"] = before
            tile_js("document.getElementById('scope-toggle').click(); true", lambda _: wait_for(badge, lambda v: v != before and v != "", second, "смена режима"), "клик")

        def second(after):
            result["badge_after"] = after
            tile_js("document.getElementById('scope-toggle').click(); true", lambda _: wait_for(badge, lambda v: v == result["badge_before"], lambda v: run_steps(), "возврат режима"), "клик")

        tile_js(badge, first, "режим")

    def tile_motion(label, start_js, expect_open, role="sidebar"):
        """Рамка плитки во время анимации: каждые 8 мс читаем положение слоя, которое сейчас видно на экране."""
        tile = host.tiles[role]
        xs, stamps = [], []

        def target_x():
            window_width = macos_frame._window(host).contentView().bounds().size.width
            width = tile.width - 16
            return 8.0 if tile.side == "left" else window_width - 8.0 - width

        def sample():
            layer = tile.glass.layer()
            presentation = layer.presentationLayer() or layer
            xs.append(presentation.frame().origin.x)
            stamps.append(time.monotonic())
            check_frame(label)
            if time.monotonic() - stamps[0] < 0.8:
                QTimer.singleShot(8, guarded(sample))
            else:
                finish()

        def finish():
            hidden = bool(tile.glass.isHidden())
            result["scenarios"][label] = {"samples": len(xs), "from": round(xs[0], 1), "to": round(xs[-1], 1), "hidden": hidden}
            monotonic_up = tile.side == "left" if expect_open else tile.side == "right"
            if expect_open:
                assert abs(xs[-1] - target_x()) < 0.5 and not hidden, f"{label}: плитка не открылась {xs[-1]} hidden={hidden}"
            else:
                window_width = macos_frame._window(host).contentView().bounds().size.width
                away = xs[-1] < -200 if tile.side == "left" else xs[-1] > window_width + 8
                assert hidden and away, f"{label}: плитка не закрылась {xs[-1]} hidden={hidden}"
            ordered = all(b >= a - 0.5 for a, b in zip(xs, xs[1:])) if monotonic_up else all(b <= a + 0.5 for a, b in zip(xs, xs[1:]))
            assert ordered, f"{label}: движение не монотонно {xs}"
            # если окно расширяется само, плитка почти не движется относительно экрана: плавность проверяем только у настоящего хода
            if abs(xs[-1] - xs[0]) > 100:
                assert len(set(round(x) for x in xs)) > 5, f"{label}: нет плавного движения {sorted(set(round(x) for x in xs))}"
            QTimer.singleShot(300, run_steps)

        start_js()
        sample()

    def open_project():
        """Правая панель есть только у открытого проекта: регистрируем временную папку, как это делает хост."""
        import urllib.request

        folder = tempfile.mkdtemp(prefix="dokky-smoke-project-")
        with open(os.path.join(folder, "hello.txt"), "w", encoding="utf-8") as handle:
            handle.write("hello")
        request = urllib.request.Request(
            f"{host.api_base}/api/projects", data=json.dumps({"path": folder}).encode(),
            headers={"Content-Type": "application/json", "X-Koddi-Desktop-Session": host.session_key}, method="POST",
        )
        with urllib.request.urlopen(request, timeout=8) as response:
            project = json.loads(response.read().decode("utf-8"))
        host.view.page().runJavaScript(f"window.koddiProjectOpened({json.dumps(project, ensure_ascii=False)})")
        started = time.monotonic()

        def poll():
            host.view.page().runJavaScript("Boolean(document.getElementById('toggle-right')) && !document.getElementById('toggle-right').hidden", guarded(got))

        def got(value):
            if value:
                result["project_opened"] = True
                QTimer.singleShot(400, run_steps)
            elif time.monotonic() - started > 10:
                raise AssertionError("проект не открылся")
            else:
                QTimer.singleShot(150, guarded(poll))

        poll()

    def inspector_content():
        wait_for("document.body.innerText.includes('Среда') && document.body.innerText.includes('Контекст')", bool,
                 lambda v: (result.update(inspector_content=True), run_steps()), "содержимое правой плитки", role="inspector")

    def inspector_cards():
        """У каждой карточки правой панели своё стекло, и его рамка совпадает с рамкой карточки на странице."""
        tile = host.tiles["inspector"]
        code = "JSON.stringify([...document.querySelectorAll('[data-glass]')].map(n => { const r = n.getBoundingClientRect(); return {id: n.dataset.glass, x: r.left, y: r.top, w: r.width, h: r.height}; }))"

        def got(raw):
            boxes = json.loads(raw)
            views = tile.regions.views
            assert len(boxes) >= 5, f"карточек на странице {len(boxes)}"
            assert set(b["id"] for b in boxes) <= set(views), f"стекла есть не у всех карточек {sorted(views)}"
            container = tile.glass
            flipped = bool(container.isFlipped())
            height = container.bounds().size.height
            for box in boxes:
                frame = views[box["id"]].frame()
                y = frame.origin.y if flipped else height - frame.origin.y - frame.size.height
                got_box = (frame.origin.x, y, frame.size.width, frame.size.height)
                want = (box["x"], box["y"], box["w"], box["h"])
                assert all(abs(a - b) <= 1.5 for a, b in zip(got_box, want)), f"{box['id']}: стекло {got_box} при рамке {want}"
            result["inspector_cards"] = len(boxes)
            run_steps()

        QTimer.singleShot(600, guarded(lambda: tile_js(code, got, "рамки карточек", "inspector")))

    def inspector_roundtrip():
        """Кнопка закрытия внутри правой плитки доходит до главного вида."""
        tile_js("document.getElementById('close-inspector').click(); true", lambda _: QTimer.singleShot(0, run_steps), "закрытие", "inspector")

    steps.append(tile_present)
    steps.append(tile_roundtrip)
    steps.append(lambda: tile_motion("плитка закрывается кнопкой в плитке", lambda: host.tiles["sidebar"].view.page().runJavaScript("document.getElementById('toggle-left').click(); true"), False))
    steps.append(lambda: tile_motion("плитка открывается из главного вида", lambda: host.menu_action("toggle-left"), True))
    steps.append(lambda: tile_motion("плитка закрывается из главного вида", lambda: host.menu_action("toggle-left"), False))
    steps.append(lambda: tile_motion("плитка открывается кнопкой главного вида", lambda: host.view.page().runJavaScript("document.getElementById('toggle-left').click(); true"), True))
    steps.append(open_project)

    def close_inspector_first():
        # открытый проект может сам открыть правую панель: сценарии идут от закрытого состояния
        if host.tiles["inspector"].open:
            host.menu_action("toggle-right")
        QTimer.singleShot(900, run_steps)

    steps.append(close_inspector_first)

    def narrow_window():
        # в узком окне открытие правой панели расширяет окно: плитка должна встать по итоговой ширине
        host.resize(1000, 700)
        QTimer.singleShot(700, run_steps)

    steps.append(narrow_window)
    steps.append(lambda: tile_motion("правая плитка открывается из главного вида", lambda: host.menu_action("toggle-right"), True, "inspector"))
    steps.append(inspector_content)
    steps.append(inspector_cards)
    steps.append(lambda: tile_motion("правая плитка закрывается кнопкой в плитке", lambda: host.tiles["inspector"].view.page().runJavaScript("document.getElementById('close-inspector').click(); true"), False, "inspector"))
    steps.append(lambda: tile_motion("правая плитка открывается кнопкой главного вида", lambda: host.view.page().runJavaScript("document.getElementById('toggle-right').click(); true"), True, "inspector"))
    steps.append(lambda: tile_motion("правая плитка закрывается из главного вида", lambda: host.menu_action("toggle-right"), False, "inspector"))

    for name, action in (
        ("меню открыть 1", "toggle-left"), ("меню закрыть 1", "toggle-left"),
        ("меню открыть 2", "toggle-left"), ("меню закрыть 2", "toggle-left"),
        ("правая панель открыть 1", "toggle-right"), ("правая панель закрыть 1", "toggle-right"),
        ("правая панель открыть 2", "toggle-right"), ("правая панель закрыть 2", "toggle-right"),
        ("режим разработки", "scope-development"), ("режим чата", "scope-chat"),
        ("меню и режим", "toggle-left"), ("меню закрыть", "toggle-left"),
    ):
        steps.append(lambda n=name, a=action: animate(n, a, lambda: QTimer.singleShot(300, run_steps)))

    def measure_region():
        """Насколько стекло под полем ввода отстаёт от его рамки на странице прямо во время ресайза."""
        def got(raw):
            tile = host.tiles.get("composer")
            view = tile.regions.views.get("composer") if tile else None
            if not raw or view is None:
                return
            box = json.loads(raw)
            frame = view.frame()
            content = macos_frame._window(host).contentView()
            y = frame.origin.y if content.isFlipped() else content.bounds().size.height - frame.origin.y - frame.size.height
            deviation = max(abs(a - b) for a, b in zip((frame.origin.x, y, frame.size.width, frame.size.height), (box["x"], box["y"], box["w"], box["h"])))
            result.setdefault("region_dev", []).append(round(deviation, 1))

        host.view.page().runJavaScript("(() => { const r = document.querySelector('.composer-field')?.getBoundingClientRect(); return r ? JSON.stringify({x: r.left, y: r.top, w: r.width, h: r.height}) : ''; })()", guarded(got))

    def resize_sweep():
        """Меняет размер окна кадр за кадром: ширина и высота, туда и обратно."""
        sizes = [(1300 - 20 * i, 800 - 10 * i) for i in range(16)] + [(1000 + 25 * i, 650 + 10 * i) for i in range(14)]
        state = {"i": 0}

        def tick():
            if state["i"] >= len(sizes):
                QTimer.singleShot(400, lambda: (check_frame("после ресайза"), run_steps()))
                return
            width, height = sizes[state["i"]]
            state["i"] += 1
            host.resize(width, height)
            QTimer.singleShot(16, lambda: (check_frame(f"ресайз {width}x{height}"), measure_region(), tick()))

        tick()

    steps.append(resize_sweep)

    def fullscreen_roundtrip():
        host.showFullScreen()

        def leave():
            assert host.isFullScreen()
            host.showNormal()
            # во время анимации выхода из полного экрана раскладкой владеет система; проверяем итог
            QTimer.singleShot(1800, guarded(lambda: (check_frame("после полного экрана"), run_steps())))

        QTimer.singleShot(1800, guarded(leave))

    steps.append(fullscreen_roundtrip)
    steps.append(lambda: animate("меню после полного экрана", "toggle-left", lambda: QTimer.singleShot(300, run_steps)))

    if "--chat" in sys.argv:
        steps[:] = [modal_over_tiles, menu_preserves_composer, lambda: check_composer("поле после меню", run_steps), pasted_text_roundtrip]

    QTimer.singleShot(600, slow_start)
    QTimer.singleShot(1700, ready)
    QTimer.singleShot(120000, app.quit)
    try:
        app.exec()
    finally:
        macos_frame.detach(host)
        backend.stop()
        web_host.desktop_server.migrate_legacy_data = migrate
        assert backend.server.config.app is init
    if result["lights_samples"] and result["lights_bad"] / result["lights_samples"] > 0.02:
        result["violations"].append(f"светофор не на месте в {result['lights_bad']} из {result['lights_samples']} кадров")
    deviations = result.get("region_dev", [])
    if deviations:
        result["region_dev_mean"] = round(sum(deviations) / len(deviations), 1)
        result["region_dev_worst"] = max(deviations)
        # стекло едет вместе с окном по предсказанию хоста; страница сама отстаёт от окна на кадры, поэтому порог только против зависшего стекла
        if result["region_dev_mean"] > 25:
            result["violations"].append(f"стекло поля ввода отстаёт при ресайзе: в среднем {result['region_dev_mean']} pt")
    result["violations"] = result["violations"][:40]
    print(json.dumps(result, ensure_ascii=False))
    if result["errors"] or result["violations"] or not result["slow_start"] or not result.get("done"):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
