"""Задержка страницы при ресайзе окна: когда окно получило ширину и когда страница увидела её (rAF) и прислала замер хосту.

Запускать из графической сессии (Терминал). Результат - JSON с задержками в мс.
"""

from __future__ import annotations

import json
import os
import tempfile
import time

os.environ["KODDI_DATA_DIR"] = tempfile.mkdtemp(prefix="dokky-resize-")
os.environ["KODDI_INTERNAL_TEST"] = "1"

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from koddi.desktop import macos_menu, splash, web_host
from koddi.desktop.transport import desktop_access

STEP_MS = int(os.getenv("STEP_MS", "16"))
RECORD = """
window.__w = []; window.__run = true;
(function tick() { window.__w.push([Date.now(), innerWidth, innerHeight]); if (window.__run) requestAnimationFrame(tick); })();
true
"""


def main():
    macos_menu.set_process_name()
    app = QApplication([])
    port = web_host._available_port()
    backend = web_host.DesktopBackend(port)
    splash.save("dark", True, (0.078, 0.078, 0.078))
    host = web_host.WebHost(port, desktop_access.key, backend)
    host.resize(1300, 800)
    backend.start()
    host.show()
    window_log, bridge_log = [], []
    original = host.resizeEvent

    def resize_event(event):
        original(event)
        window_log.append((time.time() * 1000, host.view.width(), host.view.height()))

    host.resizeEvent = resize_event
    apply = host.apply_glass

    def apply_glass(enabled, theme, color=None, regions=None, size=None):
        if size:
            bridge_log.append((time.time() * 1000, size[0], size[1]))
        apply(enabled, theme, color, regions, size)

    host.apply_glass = apply_glass
    sizes = [(1300 - 12 * i, 800 - 6 * i) for i in range(40)] + [(820 + 12 * i, 560 + 6 * i) for i in range(40)]
    state = {"i": 0}

    def tick():
        if state["i"] >= len(sizes):
            QTimer.singleShot(600, finish)
            return
        host.resize(*sizes[state["i"]])
        state["i"] += 1
        QTimer.singleShot(STEP_MS, tick)

    def finish():
        host.view.page().runJavaScript("window.__run = false; JSON.stringify(window.__w)", got)

    def first(series, width):
        for item in series:
            if item[1] == width:
                return item[0]
        return None

    def got(raw):
        page_log = json.loads(raw)
        rows = []
        for _t, width, _h in window_log[1:]:
            tw, tp = first(window_log, width), first(page_log, width)
            tb = first(bridge_log, width)
            if tp is not None and tw is not None:
                rows.append((tp - tw, None if tb is None else tb - tw))
        layout = sorted(r[0] for r in rows)
        bridge = sorted(r[1] for r in rows if r[1] is not None)
        mid = lambda v: v[len(v) // 2] if v else None  # noqa: E731
        print(json.dumps({"step_ms": STEP_MS, "window_events": len(window_log), "page_frames": len(page_log), "bridge_msgs": len(bridge_log),
                          "page_lag_ms_median": mid(layout), "page_lag_ms_p90": layout[int(len(layout) * .9)] if layout else None,
                          "bridge_lag_ms_median": mid(bridge), "bridge_lag_ms_p90": bridge[int(len(bridge) * .9)] if bridge else None}))
        app.quit()

    def start():
        host.view.page().runJavaScript(RECORD)
        QTimer.singleShot(200, tick)

    QTimer.singleShot(9000, start)
    QTimer.singleShot(60000, app.quit)
    app.exec()
    backend.stop()


if __name__ == "__main__":
    main()
