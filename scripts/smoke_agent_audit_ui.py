"""Проверка лицензий и переключателя Computer Use на подставном API без управления ОС."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "scripts"))
from koddi.licenses import catalog, notice  # noqa: E402
from smoke_next import cloud_api  # noqa: E402
from ui_testkit import browser_page  # noqa: E402


def main(output: Path) -> None:
    report = []
    for width, height in ((1100, 820), (900, 700)):
        api = cloud_api()
        value = catalog()
        state = {"enabled": False, "ok": True, "accessibility": True, "screen_capture": True}
        api.extra["/api/licenses"] = lambda *_: value
        api.extra["/api/licenses/*"] = lambda m, p, b, q: notice(*p.split("/")[-2:])
        api.extra["/api/computer/status"] = lambda *_: state

        def consent(method, path, body, query):
            state["enabled"] = body["enabled"]
            return state

        api.extra["/api/computer/consent"] = consent
        with browser_page(api, viewport=(width, height)) as page:
            page.wait_for_timeout(800)
            page.evaluate("window.koddiMenu('settings')")
            page.locator("button[data-settings-section='about']").click()
            page.get_by_role("button", name="Открыть лицензии", exact=True).click()
            page.locator("details.pref-license").first.wait_for()
            page.get_by_role("searchbox", name="Найти компонент лицензий").fill("react")
            react = (
                page.locator("details.pref-license")
                .filter(has=page.locator("summary", has_text="react 19"))
                .first
            )
            if react.count() == 0:
                react = (
                    page.locator("details.pref-license")
                    .filter(has=page.locator("summary", has_text="React"))
                    .first
                )
            react.locator("summary").click()
            react.locator("button").first.click()
            page.wait_for_function(
                "document.querySelector('pre.license-notice')?.textContent.includes('Permission is hereby granted')"
            )
            assert page.locator("pre.license-notice").evaluate(
                "(n) => n.scrollWidth <= n.clientWidth + 1"
            )
            output.mkdir(parents=True, exist_ok=True)
            page.locator("pre.license-notice").scroll_into_view_if_needed()
            page.screenshot(path=str(output / f"licenses-{width}.png"))
            page.locator("[data-settings-section=development]").click()
            toggle = page.get_by_role("switch", name="Разрешить Computer Use", exact=True)
            toggle.click()
            dialog = page.locator("dialog[open]").filter(
                has=page.get_by_text("Разрешить управление приложениями?", exact=True)
            )
            dialog.get_by_role("button", name="Разрешить", exact=True).click()
            page.wait_for_timeout(150)
            assert state["enabled"] is True
            toggle.click()
            page.wait_for_timeout(150)
            assert state["enabled"] is False
            page.screenshot(path=str(output / f"computer-consent-{width}.png"))
            assert not page.errors, page.errors
            report.append(
                {
                    "viewport": [width, height],
                    "licenses_rendered": len(value["components"]),
                    "notice_read": True,
                    "consent_enable_disable": True,
                    "page_errors": [],
                }
            )
    (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("/tmp/dokky-agent-ui"))
    main(parser.parse_args().output)
