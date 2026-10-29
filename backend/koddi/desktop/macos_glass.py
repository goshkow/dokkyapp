"""Liquid Glass на macOS 26+: сплошной цвет приложения под окном, поверх него нативные плитки стекла (macos_tiles.py).

Подложка растягивается вместе с окном средствами AppKit (autoresizing). Окно не просвечивает: стекло плиток
лежит на цвете приложения, а не на обоях рабочего стола.
"""

from __future__ import annotations

import ctypes
import json
import math
import os
import platform
import time

# вид стекла по теме: стиль (0 обычное, 1 прозрачное), оттенок rgba и затемнение подложки (множитель цвета приложения);
# значения можно подбирать окном «Настройка стекла» (Вид), оно сохраняет их в glass-look.json в папке данных
DEFAULTS = {
    "radius": 30.0,
    "dark": {"style": 0, "tint": [0.0196, 0.0196, 0.0196, 0.2], "base": 0.8},
    "light": {"style": 1, "tint": [1.0, 1.0, 1.0, 0.0], "base": 1.0},
}
SETTINGS = json.loads(json.dumps(DEFAULTS))
# радиус скругления окна по умолчанию, если система его не сообщила
DEFAULT_WINDOW_RADIUS = 32.0
# насколько страница отстаёт от окна при ресайзе, мс (замер scripts/measure_resize.py: около 41 мс, 2-3 кадра).
# Стекло под блоками встаёт по размеру окна из прошлого на столько же, поэтому едет в одном кадре с содержимым
REGION_DELAY = float(os.getenv("KODDI_REGION_DELAY", "0")) / 1000.0
# нативное стекло под блоками страницы, которые остаются в главном виде (поле ввода)
MAX_REGIONS = 8


def settings_path():
    from koddi.config import data_dir

    return data_dir() / "glass-look.json"


def _clean_settings(raw: object) -> dict:
    """Файл правит человек: берём только известные поля и держим их в допустимых пределах."""
    result = json.loads(json.dumps(DEFAULTS))
    if not isinstance(raw, dict):
        return result

    def number(value, low, high, default):
        try:
            value = float(value)
        except (TypeError, ValueError):
            return default
        return min(high, max(low, value)) if math.isfinite(value) else default

    result["radius"] = number(raw.get("radius"), 8.0, 64.0, result["radius"])
    for theme in ("dark", "light"):
        item = raw.get(theme)
        if not isinstance(item, dict):
            continue
        target = result[theme]
        target["style"] = 1 if item.get("style") == 1 else 0
        tint = item.get("tint")
        if isinstance(tint, list) and len(tint) == 4:
            target["tint"] = [number(part, 0.0, 1.0, target["tint"][i]) for i, part in enumerate(tint)]
        target["base"] = number(item.get("base"), 0.3, 1.5, target["base"])
    return result


def load_settings() -> None:
    try:
        raw = json.loads(settings_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raw = None
    SETTINGS.clear()
    SETTINGS.update(_clean_settings(raw))


def save_settings() -> None:
    settings_path().write_text(json.dumps(SETTINGS, ensure_ascii=False, indent=2), encoding="utf-8")


def reset_settings() -> None:
    SETTINGS.clear()
    SETTINGS.update(json.loads(json.dumps(DEFAULTS)))
    try:
        settings_path().unlink()
    except OSError:
        pass


def supported() -> bool:
    """Liquid Glass есть с macOS 26; на старых системах остаётся обычный вид."""
    try:
        if int(platform.mac_ver()[0].split(".")[0]) < 26:
            return False
        import AppKit
        import objc

        objc.lookUpClass("NSGlassEffectView")
        return bool(AppKit)
    except Exception:  # noqa: BLE001 - любая ошибка значит «стекла нет»
        return False


def look(theme: str):
    """Стиль и оттенок стекла; переменные KODDI_GLASS_STYLE и KODDI_GLASS_TINT переопределяют их для отладки."""
    item = SETTINGS["dark" if theme == "dark" else "light"]
    style, tint = int(item["style"]), tuple(item["tint"])
    style = int(os.getenv("KODDI_GLASS_STYLE", style))
    raw = os.getenv("KODDI_GLASS_TINT")
    if raw:
        tint = tuple(float(part) for part in raw.split(","))
    return style, tint


def dress(view, theme: str) -> None:
    """Вид NSGlassEffectView по теме: стиль, оттенок и внешний вид (тёмный или светлый)."""
    from AppKit import NSAppearance, NSColor

    style, tint = look(theme)
    view.setStyle_(style)
    view.setTintColor_(NSColor.colorWithSRGBRed_green_blue_alpha_(*tint) if tint[3] > 0 else None)
    name = "NSAppearanceNameDarkAqua" if theme == "dark" else "NSAppearanceNameAqua"
    view.setAppearance_(NSAppearance.appearanceNamed_(name))


class GlassLayer:
    def __init__(self, host):
        self.host = host
        load_settings()
        self.enabled = False
        self.theme = "dark"
        self.color = (0.078, 0.078, 0.078)
        self.background = None
        from koddi.desktop import macos_frame

        self.regions = RegionGlass(
            lambda: self._objc(self.host),
            lambda content: macos_frame.anchor_in(content, self._objc(self.host.view)),
            lambda: self.theme,
        )

    # служебное
    def _objc(self, widget):
        import objc

        return objc.objc_object(c_void_p=ctypes.c_void_p(int(widget.winId())))

    def _window(self):
        return self._objc(self.host).window()

    def _make_background(self):
        from AppKit import NSView, NSViewHeightSizable, NSViewWidthSizable

        content = self._objc(self.host)
        base = NSView.alloc().initWithFrame_(content.bounds())
        base.setAutoresizingMask_(NSViewWidthSizable | NSViewHeightSizable)
        base.setWantsLayer_(True)
        base.layer().setCornerRadius_(self.window_radius())
        base.layer().setMasksToBounds_(True)
        from koddi.desktop import macos_frame

        web = macos_frame.anchor_in(content, self._objc(self.host.view))
        content.addSubview_positioned_relativeTo_(base, -1, web)  # NSWindowBelow
        self.background = base
        return base

    def window_radius(self) -> float:
        """Системный радиус скругления окна: плитки и подложка ему концентричны."""
        # закрытый метод может вернуть меньше настоящего радиуса окна macOS 26: берём не меньше привычного
        return float(SETTINGS["radius"])

    def _paint(self):
        """Сплошной фон под плитками: цвет приложения со страницы."""
        from AppKit import NSColor

        if self.background is not None:
            factor = SETTINGS["dark" if self.theme == "dark" else "light"]["base"]
            red, green, blue = (min(1.0, part * factor) for part in self.color)
            self.background.layer().setBackgroundColor_(NSColor.colorWithSRGBRed_green_blue_alpha_(red, green, blue, 1.0).CGColor())

    def _dress(self, view):
        dress(view, self.theme)

    # публичное
    def configure(self, enabled: bool, theme: str, color=None) -> None:
        from AppKit import NSAppearance, NSColor

        self.theme = theme if theme in ("dark", "light") else "dark"
        if color is not None:
            self.color = color
        window = self._window()
        if window is None:
            return
        if enabled:
            window.setOpaque_(False)
            window.setBackgroundColor_(NSColor.clearColor())
            name = "NSAppearanceNameDarkAqua" if self.theme == "dark" else "NSAppearanceNameAqua"
            window.setAppearance_(NSAppearance.appearanceNamed_(name))
            if self.background is None:
                self._make_background()
            self.background.setHidden_(False)
            self._paint()
            self.regions.dress_all()
        else:
            window.setAppearance_(None)
            window.setOpaque_(True)
            if self.background is not None:
                self.background.setHidden_(True)
        self.enabled = enabled

    def refresh(self) -> None:
        """Применяет подобранные значения сразу: подложка, стекло под блоками и радиус."""
        if not self.enabled:
            return
        from koddi.desktop import macos_frame

        if self.background is not None:
            self.background.layer().setCornerRadius_(self.window_radius())
            self._paint()
        self.regions.dress_all()
        macos_frame.keep_look(self.host)

    @property
    def views(self) -> dict:
        return self.regions.views

    def on_resize(self) -> None:
        if self.enabled:
            self.regions.on_resize()

    def set_regions(self, regions: list[dict], size=None) -> None:
        if not self.enabled and regions:
            return
        self.regions.set_regions(regions, size)
        if self.background is not None:
            # подложка окна всегда самая нижняя, иначе она закрывает стекло
            self._objc(self.host).addSubview_positioned_relativeTo_(self.background, -1, None)


class RegionGlass:
    """Нативное стекло под блоками страницы: страница присылает рамки, стекло повторяет их и плавно едет вместе с ними.

    Используется для поля ввода главной страницы и для карточек правой плитки: у каждого блока своё стекло.
    """

    def __init__(self, container, anchor, theme, above=False, content=None):
        self.container, self.anchor, self.theme = container, anchor, theme
        # above: стекло лежит над страницей и размывает её содержимое; content: вид, который становится содержимым стекла
        self.above, self.content = above, content
        self.views: dict[str, object] = {}
        # движение, которое хост уже проигрывает: ключ -> (куда, до какого момента)
        self.flights: dict[str, tuple] = {}
        # угасание, которое хост проигрывает сам: ключ -> (вид, угасание со страницы)
        self.fades: dict[str, tuple] = {}
        self._fade_timer = None
        # как рамка блока зависит от размера страницы: во время ресайза хост двигает стекло сам, не дожидаясь страницы
        self.models: dict[str, dict] = {}
        # размеры окна по времени: стекло берёт тот, который страница показывает сейчас
        self.history: list[tuple[float, float, float]] = []
        self._follow_timer = None

    def clear(self) -> None:
        for view in self.views.values():
            view.removeFromSuperview()
        self.views.clear()
        self.flights.clear()
        self.fades.clear()
        self.models.clear()

    def dress_all(self) -> None:
        for view in self.views.values():
            dress(view.subviews()[0], self.theme())

    def set_above(self, above: bool) -> None:
        """Над страницей или под ней: пока открыто модальное окно, стекло уходит под страницу."""
        # запоминаем: стекло, которое появится позже (поле во время заставки), тоже должно встать под страницу
        self.above = above
        content = self.container()
        web = self.anchor(content)
        for view in self.views.values():
            content.addSubview_positioned_relativeTo_(view, 1 if above else -1, web)

    def _page_size(self, content, size):
        """Размер страницы в момент, когда она измерила рамки: по нему пересчитываются координаты, а не по сегодняшнему размеру окна."""
        if size and size[0] >= 1 and size[1] >= 1:
            return float(size[0]), float(size[1])
        bounds = content.bounds().size
        return float(bounds.width), float(bounds.height)

    def _learn(self, key: str, item: dict, size: tuple, flying: bool) -> None:
        """Запоминает рамку и учится по двум замерам, куда блок едет при изменении ширины и высоты."""
        box = (item["x"], item["y"], item["w"], item["h"])
        model = self.models.get(key)
        if model is None:
            # до первых двух замеров: по центру страницы блок остаётся по центру, в нижней части держится за низ, вверху - за верх
            ratio = (box[1] + box[3] / 2) / max(size[1], 1.0)
            vertical = 0.5 if 0.35 <= ratio <= 0.65 else (1.0 if ratio > 0.65 else 0.0)
            model = {"jw": [0.5, 0.0, 0.0, 0.0], "jh": [0.0, vertical, 0.0, 0.0]}
            self.models[key] = model
        elif not flying and not model["fly"]:
            dw, dh = size[0] - model["size"][0], size[1] - model["size"][1]
            old = model["box"]

            def clamp(value):
                return min(1.0, max(-1.0, value))

            if abs(dw) >= 1:
                model["jw"] = [clamp((box[0] - old[0]) / dw), 0.0, clamp((box[2] - old[2]) / dw), 0.0]
            if abs(dh) >= 1:
                model["jh"] = [0.0, clamp((box[1] - old[1]) / dh), 0.0, clamp((box[3] - old[3]) / dh)]
        model["box"], model["size"], model["fly"] = box, size, flying

    def _shown_size(self, width: float, height: float):
        """Размер, по которому страница рисуется в этот момент: окно отстаёт на REGION_DELAY."""
        history = self.history
        if not history:
            return width, height
        moment = time.monotonic() - REGION_DELAY
        if moment <= history[0][0]:
            return history[0][1], history[0][2]
        for (t0, w0, h0), (t1, w1, h1) in zip(history, history[1:]):
            if t0 <= moment <= t1:
                k = (moment - t0) / (t1 - t0) if t1 > t0 else 1.0
                return w0 + (w1 - w0) * k, h0 + (h1 - h0) * k
        return width, height

    def _predicted(self, key: str, width: float, height: float):
        model = self.models[key]
        width, height = self._shown_size(width, height)
        dw, dh = width - model["size"][0], height - model["size"][1]
        x, y, w, h = model["box"]
        jw, jh = model["jw"], model["jh"]
        return x + jw[0] * dw, y + jh[1] * dh, max(1.0, w + jw[2] * dw), max(1.0, h + jh[3] * dh)

    def on_resize(self) -> None:
        """Окно или плитка изменили размер: стекло едет по размеру, который страница показывает сейчас, страница уточняет рамку замером."""
        if not self.views:
            return
        bounds = self.container().bounds().size
        now = time.monotonic()
        self.history.append((now, float(bounds.width), float(bounds.height)))
        del self.history[:-48]
        self._place()
        self._follow()

    def _place(self) -> None:
        from AppKit import NSMakeRect

        content = self.container()
        bounds = content.bounds().size
        flipped = bool(content.isFlipped())
        for key, view in self.views.items():
            model = self.models.get(key)
            if model is None or model["fly"] or key in self.flights:
                continue
            x, y, w, h = self._predicted(key, float(bounds.width), float(bounds.height))
            view.setFrame_(NSMakeRect(x, y if flipped else float(bounds.height) - y - h, w, h))

    def _follow(self) -> None:
        """Пока отставший размер догоняет окно, стекло обновляется само: событий окна уже может не быть."""
        from PySide6.QtCore import QTimer

        if self._follow_timer is None:
            self._follow_timer = QTimer()
            self._follow_timer.setTimerType(self._follow_timer.timerType().PreciseTimer)
            self._follow_timer.setInterval(4)
            self._follow_timer.timeout.connect(self._tick)
        if not self._follow_timer.isActive():
            self._follow_timer.start()

    def _tick(self) -> None:
        if not self.history or time.monotonic() - self.history[-1][0] > REGION_DELAY + 0.03:
            self._follow_timer.stop()
            self.history.clear()
            self._place_final()
            return
        self._place()

    def _place_final(self) -> None:
        """Ресайз закончился: стекло встаёт точно по окну."""
        self._place()

    def set_regions(self, regions: list[dict], size=None) -> None:
        from AppKit import NSGlassEffectView, NSMakeRect, NSView, NSViewHeightSizable, NSViewWidthSizable

        content = self.container()
        web = self.anchor(content)
        flipped = bool(content.isFlipped())
        width, height = self._page_size(content, size)
        live = content.bounds().size
        wanted = {str(item["id"]): item for item in regions[:MAX_REGIONS]}
        for key in [key for key in self.views if key not in wanted]:
            if self.content is not None:
                # веб-вид внутри стекла остаётся живым: блок только прячется
                self.views[key].setHidden_(True)
            else:
                self.views.pop(key).removeFromSuperview()
        for key in [key for key in self.models if key not in wanted]:
            del self.models[key]

        def frame_of(box):
            x, y, w, h = (float(box[name]) for name in ("x", "y", "w", "h"))
            return NSMakeRect(x, y if flipped else height - y - h, w, h)

        now = time.monotonic()
        for key, item in wanted.items():
            target = None if os.getenv("KODDI_NO_FLIGHT") else item.get("to")
            target_key = (tuple(sorted(target.items())), item.get("at")) if target else None
            flight = self.flights.get(key)
            if target_key and flight and flight[0] == target_key and now < flight[1]:
                continue  # это движение уже идёт, повторное задание перезапустило бы его
            frame = frame_of(item)
            self._learn(key, item, (width, height), bool(target))
            if not target:
                # замер страницы мог отстать от окна: ставим рамку на сегодняшний размер, иначе стекло дёргалось бы назад
                px, py, pw, ph = self._predicted(key, live.width, live.height)
                frame = NSMakeRect(px, py if flipped else live.height - py - ph, pw, ph)
            view = self.views.get(key)
            if view is None:
                # стекло лежит в простой подложке: двигается подложка, а само стекло при движении не перерисовывается
                view = NSView.alloc().initWithFrame_(frame)
                view.setWantsLayer_(True)
                glass = NSGlassEffectView.alloc().initWithFrame_(view.bounds())
                glass.setAutoresizingMask_(NSViewWidthSizable | NSViewHeightSizable)
                view.addSubview_(glass)
                if self.content is not None:
                    glass.setContentView_(self.content())
                content.addSubview_positioned_relativeTo_(view, 1 if self.above else -1, web)
                self.views[key] = view
                dress(glass, self.theme())
            view.setHidden_(False)
            self._fade(key, view, item)
            if flight:
                view.layer().removeAllAnimations()
                self.flights.pop(key, None)
            if target:
                start, ms, curve = advance_flight(item, time.time() * 1000)
                frame = frame_of(start)
            view.setFrame_(frame)
            view.subviews()[0].setCornerRadius_(float(item.get("r", 20)))
            if target:
                if ms > 0:
                    _animate_frame(view, frame_of(target), ms, curve)
                self.flights[key] = (target_key, now + ms / 1000.0)
        for key in [key for key in self.flights if key not in wanted]:
            del self.flights[key]
        for key in [key for key in self.fades if key not in wanted]:
            del self.fades[key]

    def _fade(self, key: str, view, item: dict) -> None:
        """Блок страницы гаснет или проявляется вместе со своим слоем (переход «Чат / Поиск»), стекло - так же.

        Рамки приходят с опозданием на пару кадров, поэтому угасание по кривой и от момента начала анимации
        страницы считает хост. Анимацию Core Animation стекло не видит (рисуется по итоговой прозрачности слоя),
        поэтому прозрачность ставится каждый кадр таймером.
        """
        fade = item.get("fade")
        if not fade:
            self.fades.pop(key, None)
            view.setAlphaValue_(float(item.get("o", 1.0)))
            return
        self.fades[key] = (view, fade)
        self._fade_step()

    def _fade_step(self) -> None:
        from PySide6.QtCore import QTimer

        now_ms = time.time() * 1000
        for key, (view, fade) in list(self.fades.items()):
            motion = {"x": fade["from"], "y": 0.0, "w": 0.0, "h": 0.0, "ms": fade["ms"], "c": fade["c"], "at": fade["at"],
                      "to": {"x": fade["to"], "y": 0.0, "w": 0.0, "h": 0.0}}
            value, left, _curve = advance_flight(motion, now_ms)
            view.setAlphaValue_(min(1.0, max(0.0, value["x"])))
            if left <= 0:
                del self.fades[key]
        if self._fade_timer is None:
            self._fade_timer = QTimer()
            self._fade_timer.setTimerType(self._fade_timer.timerType().PreciseTimer)
            self._fade_timer.setInterval(8)
            self._fade_timer.timeout.connect(self._fade_step)
        if self.fades and not self._fade_timer.isActive():
            self._fade_timer.start()
        elif not self.fades:
            self._fade_timer.stop()


def advance_flight(item: dict, now_ms: float):
    """Учитывает время доставки из WebChannel, сохраняя оставшуюся часть кривой."""
    ms, curve = item["ms"], item["c"]
    t = min(1.0, max(0.0, (now_ms - item.get("at", now_ms)) / ms))
    if t >= 1:
        return item["to"], 0, curve
    x1, y1, x2, y2 = curve

    def cubic(a, b, u):
        return 3 * a * u * (1 - u) ** 2 + 3 * b * u * u * (1 - u) + u ** 3

    low, high = 0.0, 1.0
    for _ in range(24):
        mid = (low + high) / 2
        if cubic(x1, x2, mid) < t:
            low = mid
        else:
            high = mid
    u = (low + high) / 2
    fx, fy = cubic(x1, x2, u), cubic(y1, y2, u)
    lerp = lambda a, b: a + (b - a) * u  # noqa: E731
    ex = lerp(lerp(x1, x2), lerp(x2, 1))
    ey = lerp(lerp(y1, y2), lerp(y2, 1))
    cx, cy = lerp(x2, 1), lerp(y2, 1)
    width, height = 1 - fx or 1, 1 - fy or 1
    tail = [(ex - fx) / width, (ey - fy) / height, (cx - fx) / width, (cy - fy) / height]
    start = {name: item[name] + (item["to"][name] - item[name]) * fy for name in ("x", "y", "w", "h")}
    return start, ms * (1 - t), tail


def _animate_frame(view, frame, ms, curve):
    import objc
    from AppKit import NSAnimationContext

    timing = objc.lookUpClass("CAMediaTimingFunction").functionWithControlPoints____(*curve)

    def changes(context):
        context.setDuration_(ms / 1000.0)
        context.setTimingFunction_(timing)
        context.setAllowsImplicitAnimation_(True)
        view.animator().setFrame_(frame)

    NSAnimationContext.runAnimationGroup_completionHandler_(changes, None)


def clean_regions(payload: object) -> list[dict]:
    """Данные пришли из страницы: берём только известные числовые поля."""
    result = []
    for item in payload if isinstance(payload, list) else []:
        try:
            region = {"id": str(item["id"])[:48]}
            for name in ("x", "y", "w", "h"):
                value = float(item[name])
                if not -10000 < value < 10000:
                    raise ValueError
                region[name] = value
            region["r"] = min(200.0, max(0.0, float(item.get("r", 20))))
            if "o" in item:
                opacity = float(item["o"])
                if not math.isfinite(opacity):
                    raise ValueError
                region["o"] = min(1.0, max(0.0, opacity))
            fade = item.get("fade")
            if fade:
                region["fade"] = {name: float(fade[name]) for name in ("from", "to", "ms", "at")}
                region["fade"]["c"] = [float(value) for value in fade["c"]]
                if (
                    not all(0.0 <= region["fade"][name] <= 1.0 for name in ("from", "to"))
                    or not 0 < region["fade"]["ms"] <= 2000
                    or not (math.isfinite(region["fade"]["at"]) and region["fade"]["at"] > 0)
                    or len(region["fade"]["c"]) != 4
                    or not all(-2 <= value <= 3 for value in region["fade"]["c"])
                ):
                    raise ValueError
            target = item.get("to")
            if target:
                region["to"] = {name: float(target[name]) for name in ("x", "y", "w", "h")}
                if not all(-10000 < value < 10000 for value in region["to"].values()):
                    raise ValueError
                region["ms"] = min(2000.0, max(16.0, float(item["ms"])))
                curve = [float(value) for value in item["c"]]
                if len(curve) != 4 or not all(-2 <= value <= 3 for value in curve):
                    raise ValueError
                region["c"] = curve
                if "at" in item:
                    at = float(item["at"])
                    if not math.isfinite(at) or at < 0:
                        raise ValueError
                    region["at"] = at
        except (KeyError, TypeError, ValueError):
            continue
        if region["w"] >= 1 and region["h"] >= 1:
            result.append(region)
    return result[:MAX_REGIONS]


def clean_color(value: object):
    """Цвет фона приложения от страницы: три числа 0..1 или ничего."""
    try:
        red, green, blue = (float(part) for part in value)  # type: ignore[union-attr]
    except (TypeError, ValueError):
        return None
    return (red, green, blue) if all(0.0 <= part <= 1.0 for part in (red, green, blue)) else None
