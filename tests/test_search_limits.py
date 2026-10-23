import threading
import time

from koddi.tools import ToolRunner


def test_pathological_search_is_bounded(tmp_path):
    (tmp_path / "text.txt").write_text("a" * 20000 + "!", encoding="utf-8")
    started = time.monotonic()
    result = ToolRunner(tmp_path).run("search.text", {"query": "(a+)+$"})
    assert not result["ok"]
    assert time.monotonic() - started < 2


def test_cancel_search_before_read(tmp_path):
    cancelled = threading.Event()
    cancelled.set()
    result = ToolRunner(tmp_path, cancel_event=cancelled).run("search.text", {"query": "hello"})
    assert result["cancelled"]


def test_tool_output_masks_secrets(tmp_path):
    secret = "sk-" + "A" * 30
    (tmp_path / "config.py").write_text(f"api_key='{secret}'", encoding="utf-8")
    result = ToolRunner(tmp_path).run("file.read", {"path": "config.py"})
    assert secret not in str(result)
    assert "[redacted]" in str(result)
