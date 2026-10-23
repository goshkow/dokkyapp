from pathlib import Path
import re

WEB = Path("backend/koddi/desktop/web")


def test_shell_has_required_working_surfaces_and_local_dependencies():
    html = (WEB / "index.html").read_text(encoding="utf-8")
    for name in (
        "feed",
        "composer",
        "prompt",
        "send",
        "workspace-selector",
        "file-tree",
        "editor-view",
        "task-plan",
        "request-panel",
        "chat-menu",
        "segmented-mode",
    ):
        assert f'id="{name}"' in html
    for filename in ("marked.js", "purify.js", "lucide.js", "highlight.js"):
        assert (WEB / "vendor" / filename).is_file()
        assert f"vendor/{filename}" in html
    assert (WEB / "assets/mark.png").is_file()


def test_chat_uses_token_stream_and_independent_task_lifecycle():
    source = (WEB / "app.js").read_text(encoding="utf-8")
    for contract in (
        "message.delta",
        "message_id",
        "streamResponse",
        "tool.progress",
        "tool.call",
        "tool.result",
        "plan.updated",
        "agent.input_required",
        "stream.reset",
        "turn/events",
        "followFeed",
        "requestAnimationFrame",
        "turn/cancel",
    ):
        assert contract in source
    assert "const parsed = marked.parse(" in source
    assert "const fragment = DOMPurify.sanitize(" in source
    assert "rich ? richChecklist(parsed) : parsed" in source
    assert "target.replaceChildren(fragment)" in source
    assert "num_predict" not in source
    assert "/api/ollama" not in source
    assert "/api/huggingface" not in source


def test_desktop_host_preserves_native_resize_move_and_dwm():
    host = Path("backend/koddi/desktop/web_host.py").read_text(encoding="utf-8")
    for contract in (
        "startSystemMove()",
        "startSystemResize(self.edges)",
        "DwmSetWindowAttribute(hwnd, 33",
        "QWebEngineScript",
        "setHttpUserAgent",
        "KoddiDesktop/",
        "_available_port()",
    ):
        assert contract in host
    assert "_apply_corner_mask" not in host


def test_loading_scrolling_and_mobile_constraints():
    css = (WEB / "app.css").read_text(encoding="utf-8")
    source = (WEB / "app.js").read_text(encoding="utf-8")
    assert "Promise.all" in source
    assert "viewVersion" in source and "AbortController" in source
    assert ".skeleton" in css and "@keyframes shimmer" in css
    assert re.search(r"scrollbar-color:\s*#414141\s+transparent", css)
    assert "prefers-reduced-motion" in css
    assert re.search(r"max-width:\s*720px", css)
    assert "transition:grid-template-columns" not in css
