"""Боковые панели как нативные плитки Liquid Glass.

Левое меню: стекло - NSGlassEffectView, а содержимое плитки (свой веб-вид) лежит внутри него.
Правая панель (роль inspector): сплошной фон, а у каждой карточки свой NSGlassEffectView под прозрачным веб-видом:
страница присылает рамки карточек (RegionGlass), и стекло повторяет их.

Так устроены и нативные приложения: AppKit сам двигает рамку плитки, а содержимое подстраивается автоматически,
поэтому стекло и содержимое не расходятся ни при анимации панели, ни при изменении размера окна.
"""

from __future__ import annotations

import ctypes

from PySide6.QtCore import QUrl

GAP = 8
OFFSCREEN_PAD = 24


def _nsview(widget):
    import objc

    return objc.objc_object(c_void_p=ctypes.c_void_p(int(widget.winId())))


class PanelTile:
    """Плитка у левого или правого края окна: стекло с веб-видом роли role (sidebar, inspector)."""

    def __init__(self, host, role: str, side: str):
        from koddi.desktop.native_web import MacWebView

        self.host, self.role, self.side = host, role, side
        self.open, self.width = False, 288.0 if side == "left" else 360.0
        self.animating = False
        self._token = 0
        self.glass = None
        self.theme = "dark"
        # у правой панели стекло не одно на всю плитку, а по своему на каждую карточку
        self.cards = role == "inspector"
        self.regions = None
        # страница плитки: то же хранилище и тот же мост окна, что у главной
        self.view = MacWebView(host, host.centralWidget(), role)
        self.bridge, self.channel = host._install_page(self.view, role)
        self.view.setGeometry(0, 0, 280, 400)
        self.view.show()
        self.view.loadFinished.connect(lambda _ok: host.apply_lights(self.view))
        self.view.load(QUrl(f"{host.api_base}/ui/?role={role}"))

    # нативная часть
    def attach(self) -> None:
        """Кладёт веб-вид внутрь нового NSGlassEffectView, который лежит над главным веб-видом."""
        from AppKit import NSGlassEffectView, NSMakeRect, NSViewHeightSizable

        from koddi.desktop import macos_frame

        content = self.host.glass_content_view()
        web = macos_frame.anchor_in(content, _nsview(self.host.view))
        if self.cards:
            from AppKit import NSView, NSViewWidthSizable
            from koddi.desktop import macos_glass

            glass = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, 200, 200))
            glass.setWantsLayer_(True)
            glass.setAutoresizingMask_(NSViewHeightSizable)
            content.addSubview_positioned_relativeTo_(glass, 1, web)
            page = _nsview(self.view)
            page.setFrame_(glass.bounds())
            page.setAutoresizingMask_(NSViewWidthSizable | NSViewHeightSizable)
            glass.addSubview_(page)
            self.regions = macos_glass.RegionGlass(lambda: glass, lambda container: macos_frame.anchor_in(container, page), lambda: self.theme)
        else:
            glass = NSGlassEffectView.alloc().initWithFrame_(NSMakeRect(0, 0, 200, 200))
            # концентрично окну: системный радиус минус отступ плитки от края
            glass.setCornerRadius_(max(8.0, self.host.glass.window_radius() - GAP))
            # по горизонтали плитку ставит layout(): автоматическое смещение у правого края спорило бы с анимацией и ростом окна
            glass.setAutoresizingMask_(NSViewHeightSizable)
            content.addSubview_positioned_relativeTo_(glass, 1, web)  # NSWindowAbove
            glass.setContentView_(_nsview(self.view))
        self.glass = glass
        self.dress(self.theme)
        self.layout(animate=False)

    def set_overlay(self, active: bool) -> None:
        """Пока над страницей открыто модальное окно, плитка лежит под страницей (её колонка прозрачна) и окно ложится поверх."""
        if self.glass is None:
            return
        from koddi.desktop import macos_frame

        content = self.host.glass_content_view()
        web = macos_frame.anchor_in(content, _nsview(self.host.view))
        content.addSubview_positioned_relativeTo_(self.glass, -1 if active else 1, web)  # ниже или выше страницы

    def dress(self, theme: str) -> None:
        from koddi.desktop import macos_glass

        self.theme = theme
        if self.cards:
            if self.regions is not None:
                self.regions.dress_all()
        elif self.glass is not None:
            macos_glass.dress(self.glass, theme)

    def apply_radius(self) -> None:
        if self.glass is not None and not self.cards:
            self.glass.setCornerRadius_(max(8.0, self.host.glass.window_radius() - GAP))

    def set_regions(self, regions: list[dict], size=None) -> None:
        """Рамки карточек от страницы правой панели: у каждой своё стекло."""
        if self.regions is not None:
            self.regions.set_regions(regions, size)

    def _frames(self, open_: bool):
        from AppKit import NSMakeRect

        content = self.host.glass_content_view()
        bounds = content.bounds()
        # во время роста окна правая плитка встаёт по итоговой ширине
        total = float(self.host._final_width or bounds.size.width)
        height = float(bounds.size.height)
        width = max(120.0, self.width - 2 * GAP)
        h = max(0.0, height - 2 * GAP)
        if self.side == "left":
            x = GAP if open_ else -(width + GAP + OFFSCREEN_PAD)
        else:
            x = total - GAP - width if open_ else total + GAP + OFFSCREEN_PAD
        return NSMakeRect(x, GAP, width, h)

    def on_window_resize(self) -> None:
        """Окно меняет размер: плитка следует за краем, пока не идёт её собственная анимация."""
        if self.glass is not None and not self.animating:
            self.layout(animate=False)
            if self.regions is not None:
                self.regions.on_resize()

    def layout(self, animate: bool = False, ms: float = 0.0, curve=None, open_: bool | None = None, width: float | None = None) -> None:
        """Ставит плитку на место; с animate рамку двигает AppKit одной анимацией (с тем же безье, что у страницы)."""
        if self.glass is None:
            return
        import objc
        from AppKit import NSAnimationContext

        if width is not None:
            self.width = float(width)
        if open_ is not None:
            self.open = bool(open_)
        target = self._frames(self.open)
        visible = self.host.glass is not None and self.host.glass.enabled
        if not visible:
            self.glass.setHidden_(True)
            self.glass.setFrame_(target)
            return
        if not animate or ms <= 0:
            self.animating = False
            self._token += 1
            layer = self.glass.layer()  # у ещё не показанного стекла слоя может не быть
            if layer is not None:
                layer.removeAllAnimations()
            self.glass.setFrame_(target)
            self.glass.setHidden_(not self.open)
            return
        # закрытая плитка стоит за краем окна: при открытии её нужно показать до начала движения
        self.glass.setHidden_(False)
        timing = None
        if curve and len(curve) == 4:
            timing = objc.lookUpClass("CAMediaTimingFunction").functionWithControlPoints____(*[float(v) for v in curve])
        open_now = self.open

        def changes(context):
            context.setDuration_(ms / 1000.0)
            if timing is not None:
                context.setTimingFunction_(timing)
            context.setAllowsImplicitAnimation_(True)
            self.glass.animator().setFrame_(target)

        self.animating = True
        self._token += 1
        token = self._token

        def done():
            # завершение прерванной анимации приходит позже новой: оно не должно снимать флаг у идущей
            if token != self._token:
                return
            self.animating = False
            if not open_now and not self.open:
                self.glass.setHidden_(True)
            self.layout(animate=False)  # итоговое положение по настоящим размерам окна

        NSAnimationContext.runAnimationGroup_completionHandler_(changes, done)


class ComposerTile:
    """Поле ввода: нативное стекло лежит над главным веб-видом и само размывает ленту под собой, а поле (свой веб-вид) - внутри стекла."""

    def __init__(self, host):
        from koddi.desktop import macos_frame, macos_glass
        from koddi.desktop.native_web import MacWebView

        self.host, self.role = host, "composer"
        self.theme = "dark"
        self.view = MacWebView(host, host.centralWidget(), "composer")
        self.bridge, self.channel = host._install_page(self.view, "composer")
        # вид грузится сразу, но до первой рамки поля стоит за окном: без стекла рамок нет вовсе,
        # и в углу (0,0) он висел дубликатом поля ввода
        self.view.setGeometry(-10000, -10000, 400, 80)
        self.view.show()
        self.view.loadFinished.connect(lambda _ok: host.apply_lights(self.view))
        self.view.load(QUrl(f"{host.api_base}/ui/?role=composer"))
        self.regions = macos_glass.RegionGlass(
            host.glass_content_view,
            lambda content: macos_frame.anchor_in(content, _nsview(host.view)),
            lambda: self.theme,
            above=True,
            content=lambda: _nsview(self.view),
        )

    def set_regions(self, regions: list[dict], size=None) -> None:
        self.regions.set_regions(regions, size)

    def attach(self) -> None:
        """Стекло создаётся, когда страница сообщает рамку поля."""

    def dress(self, theme: str) -> None:
        self.theme = theme
        self.regions.dress_all()

    def apply_radius(self) -> None:
        """Радиус стекла поля задаёт страница (скругление самого поля)."""

    def set_overlay(self, active: bool) -> None:
        self.regions.set_above(not active)

    def on_window_resize(self) -> None:
        self.regions.on_resize()

    def layout(self, animate: bool = False, **_kwargs) -> None:
        """Положение стекла задаёт рамка поля со страницы."""
