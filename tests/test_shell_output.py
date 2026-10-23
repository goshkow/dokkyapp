import os
import re
import threading
import time

import pytest

from koddi.tools import ToolRunner


@pytest.mark.skipif(os.name != "nt", reason="Windows shell integration")
def test_large_shell_output_cannot_deadlock_and_is_bounded(tmp_path):
    started = time.monotonic()
    result = ToolRunner(tmp_path).shell_run(
        {
            "command": (
                "[Console]::Out.Write(('OUT-HEAD|' + ('x' * 200000) + '|OUT-TAIL')); "
                "[Console]::Error.Write(('ERR-HEAD|' + ('e' * 50000) + '|ERR-TAIL'))"
            ),
            "timeout_seconds": 15,
        }
    )
    assert time.monotonic() - started < 20
    assert result["ok"], result
    assert not result.get("output_incomplete"), result
    for key, source, limit in (
        ("stdout", "OUT-HEAD|" + "x" * 200000 + "|OUT-TAIL", 20000),
        ("stderr", "ERR-HEAD|" + "e" * 50000 + "|ERR-TAIL", 12000),
    ):
        output = result[key]
        assert len(output) <= limit
        marker = re.search(
            r"\n\.\.\.\[пропущено (\d+) символов, (\d+) строк\]\.\.\.\n", output
        )
        assert marker is not None
        head, tail = output[:marker.start()], output[marker.end():]
        assert head.startswith(source[:9]) and tail.endswith(source[-9:])
        assert source.startswith(head) and source.endswith(tail)
        assert int(marker[1]) == len(source) - len(head) - len(tail)
        assert int(marker[2]) == 1


@pytest.mark.skipif(os.name != "nt", reason="Windows shell integration")
def test_running_shell_cancels_promptly(tmp_path):
    cancel = threading.Event()
    runner = ToolRunner(tmp_path, cancel_event=cancel)
    timer = threading.Timer(1, cancel.set)
    timer.start()
    started = time.monotonic()
    try:
        result = runner.shell_run({"command": "Start-Sleep -Seconds 30", "timeout_seconds": 40})
    finally:
        timer.cancel()
    assert result.get("cancelled"), result
    assert time.monotonic() - started < 10


@pytest.mark.skipif(os.name != "nt", reason="Windows shell integration")
def test_shell_preserves_cyrillic_output(tmp_path):
    result = ToolRunner(tmp_path).shell_run(
        {"command": "Write-Output 'Привет!'", "timeout_seconds": 15}
    )
    assert result["ok"] and result["stdout"].strip() == "Привет!"
