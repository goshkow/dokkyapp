import io
import json
import re
import threading
from copy import deepcopy
from types import SimpleNamespace

import pytest

import koddi.storage.db as storage
import koddi.tools.runner as runner_module
from koddi.tools.runner import ToolError, ToolRunner, _ToolOutputBuffer, tool_output_excerpt


@pytest.fixture(autouse=True)
def isolated_profile(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DB_PATH", tmp_path / "profile" / "stability.sqlite3")
    monkeypatch.setenv("KODDI_DATA_DIR", str(tmp_path / "profile"))
    storage.init_db()
    storage.set_setting("permission_profile", "trusted")


def assert_excerpt(source, result, limit):
    source = source.replace("\r\n", "\n")
    match = re.search(r"\n\.\.\.\[пропущено (\d+) символов, (\d+) строк\]\.\.\.\n", result)
    assert match is not None
    head, tail = result[:match.start()], result[match.end():]
    assert source.startswith(head) and source.endswith(tail)
    omitted = source[len(head):len(source) - len(tail)]
    assert int(match[1]) == len(omitted)
    assert int(match[2]) == omitted.count("\n") + int(not omitted.endswith("\n"))
    assert len(result) <= limit


@pytest.mark.parametrize("text", [
    "prefix " + "text " * 10000 + " end",
    "".join(f"Строка {index}\r\n" for index in range(10000)),
    "界" * 30000 + "\nКонец",
], ids=["single-line", "crlf-lines", "unicode"])
@pytest.mark.parametrize("limit", [128, 2000, 20000])
def test_excerpt_has_exact_counts_and_is_idempotent(text, limit):
    result = tool_output_excerpt(text, limit)
    assert_excerpt(text, result, limit)
    assert tool_output_excerpt(text, limit) == result
    assert tool_output_excerpt(result, limit) == result


def test_small_output_is_not_cut_or_trimmed():
    assert tool_output_excerpt("  first\r\nsecond\r\n  ") == "  first\nsecond\n  "
    assert tool_output_excerpt("x" * 128, 128) == "x" * 128
    assert tool_output_excerpt("") == ""
    with pytest.raises(ValueError):
        tool_output_excerpt("text", 127)


def test_stream_chunk_boundaries_do_not_change_excerpt_or_memory_bound():
    text = "".join(f"line {index}\r\n" for index in range(20000)) + "last\r"
    expected = tool_output_excerpt(text, 2000)
    for chunk_size in (1, 7, 4096):
        output = _ToolOutputBuffer(2000)
        for start in range(0, len(text), chunk_size):
            output.append(text[start:start + chunk_size])
            assert len(output.head) <= 2000 and len(output.tail) <= 2001
        assert output.finish() == expected
        assert output.finish() == expected


def test_search_result_sorted_by_path_and_numeric_line_without_mutation():
    matches = [
        {"path": "z.py", "line": 1, "text": "last"},
        {"path": "a.py", "line": 10, "text": "ten"},
        {"path": "a.py", "line": 2, "text": "two"},
    ]
    before = deepcopy(matches)
    result = ToolRunner._search_result(matches, ["z.bin", "a.bin"], False)
    assert [(item["path"], item["line"]) for item in result["matches"]] == [
        ("a.py", 2), ("a.py", 10), ("z.py", 1),
    ]
    assert result["stdout"] == "a.py:2:two\na.py:10:ten\nz.py:1:last"
    assert result["skipped"] == ["a.bin", "z.bin"]
    assert matches == before


def test_directory_and_search_lists_have_stable_path_order(tmp_path):
    workspace = tmp_path / "workspace"
    (workspace / "a").mkdir(parents=True)
    for path in ("z.txt", "b.txt", "a/z.txt", "a/a.txt"):
        (workspace / path).write_text("match\nmatch", encoding="utf-8")
    runner = ToolRunner(workspace)
    for name, args, key in (
        ("directory.tree", {}, "items"),
        ("search.files", {"query": "*.txt"}, "files"),
        ("search.text", {"query": "match"}, "matches"),
    ):
        first = runner.run(name, args)
        second = runner.run(name, args)
        assert first == second and first["ok"]
        paths = [item if isinstance(item, str) else item["path"] for item in first[key]]
        assert paths == sorted(paths)


@pytest.mark.parametrize("name,key", [("search.files", "files"), ("search.text", "matches")])
def test_search_cap_selects_same_paths_before_sorting_results(tmp_path, monkeypatch, name, key):
    paths = []
    for index in range(505):
        path = tmp_path / f"file-{index:04d}.txt"
        path.write_text("match", encoding="utf-8")
        paths.append(path)
    runner = ToolRunner(tmp_path)
    monkeypatch.setattr(runner_module.WorkspacePaths, "files", lambda self: iter(reversed(paths)))
    result = runner.run(name, {"query": "*.txt" if name == "search.files" else "match"})
    assert result["ok"] and result["truncated"]
    actual = [item if isinstance(item, str) else item["path"] for item in result[key]]
    assert actual == [path.name for path in paths[:500]]


@pytest.mark.parametrize("name", ["search.files", "search.text"])
def test_search_candidate_bound_fails_before_sort_or_partial_result(tmp_path, monkeypatch, name):
    consumed = 0

    class UnsortedPath:
        def relative_to(self, workspace):
            pytest.fail("Превышенный список не должен сортироваться")

    path = UnsortedPath()

    def candidates(self):
        nonlocal consumed
        for _ in range(runner_module.MAX_SEARCH_CANDIDATE_PATHS + 50):
            consumed += 1
            yield path

    monkeypatch.setattr(runner_module.WorkspacePaths, "files", candidates)
    result = ToolRunner(tmp_path).run(name, {"query": "match"})
    assert consumed == runner_module.MAX_SEARCH_CANDIDATE_PATHS + 1
    assert result["ok"] is False
    assert "лимит 100000 файлов-кандидатов" in result["error"]
    assert "matches" not in result and "files" not in result


def test_search_checks_cancellation_after_sort(tmp_path, monkeypatch):
    runner = ToolRunner(tmp_path)

    class CancelDuringSort:
        def relative_to(self, workspace):
            runner.cancel_event.set()
            return SimpleNamespace(as_posix=lambda: "test.txt")

    monkeypatch.setattr(runner_module.WorkspacePaths, "files", lambda self: iter([CancelDuringSort()]))
    with pytest.raises(ToolError, match="search cancelled"):
        runner._search_paths(float("inf"))


def test_runner_bounds_new_stdout_before_journal_but_keeps_full_audit(tmp_path, monkeypatch):
    raw = {
        "ok": True, "stdout": "head\n" + "middle\n" * 12000 + "tail\n", "stderr": "",
        "output_id": "out_1_2_3", "output_file": "C:/runtime/result.json",
        "path": "src/main.py", "session_id": 123, "created_at": "2026-10-16T12:00:00Z",
    }
    before = deepcopy(raw)
    runner = ToolRunner(tmp_path)
    monkeypatch.setattr(runner, "_dispatch", lambda name, args: raw)
    result = runner.run("git.status", {})
    assert_excerpt(before["stdout"], result["stdout"], 20000)
    for key in raw.keys() - {"stdout"}:
        assert result[key] == raw[key]
    with storage.db() as conn:
        audit = json.loads(conn.execute("SELECT result_json FROM tool_calls").fetchone()[0])
    assert audit == before and raw == before
    journal = json.dumps({"role": "tool", "tool_call_id": "call-exact", "content": json.dumps(result, ensure_ascii=False)}, ensure_ascii=False)
    assert json.loads(journal)["tool_call_id"] == "call-exact"
    assert "пропущено" in journal


def test_file_payload_hash_ranges_and_actual_bytes_are_not_normalized(tmp_path):
    target = tmp_path / "source.txt"
    data = b"first\r\n" + b"unchanged data\r\n" * 4000 + b"last\r\n"
    target.write_bytes(data)
    runner = ToolRunner(tmp_path)
    result = runner.run("file.read", {"path": "source.txt", "max_bytes": len(data)})
    assert result["content"] == data.decode("utf-8")
    assert result["path"] == "source.txt" and result["sha256"]
    chunk = runner.run("file.read_chunk", {"path": "source.txt", "offset": 7, "size": 30000})
    assert chunk["offset"] == 7 and chunk["content"] == data[7:30007].decode("utf-8")
    assert chunk["chunk_sha256"] and target.read_bytes() == data


def test_long_search_line_keeps_path_line_and_tail(tmp_path):
    line = "needle " + "data " * 2000 + " important-tail"
    (tmp_path / "source.txt").write_text(line, encoding="utf-8")
    result = ToolRunner(tmp_path).run("search.text", {"query": "needle"})
    match = result["matches"][0]
    assert match["path"] == "source.txt" and match["line"] == 1
    assert_excerpt(line, match["text"], 2000)
    assert match["text"].endswith("important-tail")


def test_shell_stream_keeps_head_tail_and_exit_status(tmp_path, monkeypatch):
    text = "START\n" + "payload\n" * 10000 + "FINAL\n"
    process = SimpleNamespace(
        stdout=io.StringIO(text), stderr=io.StringIO("error details\n"),
        returncode=7, poll=lambda: 7,
    )
    monkeypatch.setattr(runner_module.subprocess, "Popen", lambda *args, **kwargs: process)
    result = ToolRunner(tmp_path).shell_run({"command": "test fixture"})
    assert result["returncode"] == 7 and not result["ok"]
    assert result["stdout"].startswith("START\n") and result["stdout"].endswith("FINAL\n")
    assert_excerpt(text, result["stdout"], 20000)
    assert result["stderr"] == "error details\n"


def test_shell_keeps_collected_output_when_child_holds_pipe_open(tmp_path, monkeypatch):
    waiting = threading.Event()
    release = threading.Event()
    threads = []
    thread_type = threading.Thread
    real_join = thread_type.join

    class DelayedStream(io.StringIO):
        def read(self, size):
            result = super().read(size)
            if not result:
                waiting.set()
                release.wait(5)
            return result

    def start_reader(*args, **kwargs):
        thread = thread_type(*args, **kwargs)
        thread.join = lambda timeout: None
        threads.append(thread)
        return thread

    def poll():
        assert waiting.wait(2)
        return 0

    process = SimpleNamespace(
        stdout=DelayedStream("collected before parent exit\n"), stderr=io.StringIO(""),
        returncode=0, poll=poll,
    )
    monkeypatch.setattr(runner_module.threading, "Thread", start_reader)
    monkeypatch.setattr(runner_module.subprocess, "Popen", lambda *args, **kwargs: process)
    try:
        result = ToolRunner(tmp_path).shell_run({"command": "test fixture"})
        assert result["stdout"] == "collected before parent exit\n"
        assert result["output_incomplete"] is True
    finally:
        release.set()
        for thread in threads:
            real_join(thread, timeout=2)
