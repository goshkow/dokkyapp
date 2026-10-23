"""Офлайн-воспроизведение отказа по Origin на экране согласия Докки."""

import json

from playwright.sync_api import sync_playwright


def main():
    result = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        for policy in ("no-referrer", "same-origin", "strict-origin-when-cross-origin"):
            context = browser.new_context()
            page = context.new_page()
            captured = {}

            def route(request_route):
                request = request_route.request
                if request.method == "POST":
                    captured.update(
                        {
                            "policy": policy,
                            "origin": request.headers.get("origin"),
                            "sec-fetch-site": request.headers.get("sec-fetch-site"),
                        }
                    )
                    request_route.fulfill(body="Проверка завершена", content_type="text/html")
                else:
                    request_route.fulfill(
                        body=f'<meta name="referrer" content="{policy}">'
                        '<form method="post" action="/oauth/authorize/decision">'
                        '<button type="submit">Продолжить</button></form>',
                        headers={
                            "Content-Type": "text/html; charset=utf-8",
                            "Referrer-Policy": policy,
                        },
                    )

            page.route("**/*", route)
            page.goto("https://dokky.goshkow.com/oauth/authorize")
            page.get_by_role("button").click()
            page.wait_for_url("**/oauth/authorize/decision")
            result.append(captured)
            context.close()
        browser.close()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    assert result[0]["origin"] == "null", result
    assert result[1]["origin"] == "https://dokky.goshkow.com", result
    assert result[2]["origin"] == "https://dokky.goshkow.com", result


if __name__ == "__main__":
    main()
