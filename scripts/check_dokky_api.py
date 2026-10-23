"""Проверки боевого API, которые ничего не списывают: ни ключей, ни запросов к модели."""

import asyncio
import json

import httpx

from koddi.dokky.client import API, DokkyClient


async def main():
    async with httpx.AsyncClient(timeout=25, follow_redirects=False) as http:
        response = await http.get(API + "/openapi.json")
        response.raise_for_status()
        schema = response.json()
        required = {
            "/bootstrap": "get",
            "/models": "get",
            "/chats": "post",
            "/chats/{chat_id}/messages": "post",
            "/runs/{run_id}/events": "get",
            "/runs/{run_id}/cancel": "post",
            "/chat/completions": "post",
            "/files": "post",
            "/events": "get",
            "/speech/transcriptions": "post",
        }
        missing = [
            f"{method} {path}"
            for path, method in required.items()
            if method not in schema.get("paths", {}).get(path, {})
        ]
        checks = []
        for path in ("/bootstrap", "/me", "/models", "/chats", "/events"):
            reply = await http.get(API + path)
            try:
                code = reply.json().get("error", {}).get("code")
            except (ValueError, AttributeError):
                code = None
            checks.append({"path": path, "status": reply.status_code, "error_code": code})
        flow = await DokkyClient().begin("http://127.0.0.1:8878/api/dokky/auth/callback")
        login = await http.get(flow["authorize_url"])
        report = {
            "api": API,
            "schema_version": schema.get("info", {}).get("version"),
            "missing_routes": missing,
            "unauthenticated_checks": checks,
            "authorize_status": login.status_code,
            "authorize_is_html": "text/html" in login.headers.get("content-type", ""),
            "authorize_referrer_policy": login.headers.get("referrer-policy"),
            "authorize_meta_same_origin": 'name="referrer" content="same-origin"' in login.text,
            "authenticated_flow": "not_tested",
        }
        print(json.dumps(report, ensure_ascii=False, indent=2))
        assert not missing, missing
        assert all(
            item["status"] == 401 and item["error_code"] == "invalid_token" for item in checks
        )
        assert login.status_code == 200 and report["authorize_is_html"]


if __name__ == "__main__":
    asyncio.run(main())
