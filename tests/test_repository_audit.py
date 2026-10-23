import runpy
from pathlib import Path


AUDIT = runpy.run_path(str(Path(__file__).resolve().parents[1] / "scripts/check_repository.py"))


def test_runtime_files_are_rejected_but_build_sources_are_allowed():
    check = AUDIT["is_runtime_path"]
    for path in (
        "dist_koddi_test/web_host.dist/Koddi.exe", "backend/a/__pycache__/a.pyc",
        ".koddi-data/history.sqlite3-wal", ".env", "private.pem", "log.txt.log",
        "backend/koddi.egg-info/PKG-INFO", "test-results/report.json",
    ):
        assert check(path), path
    for path in (
        "scripts/build_with_budget.py", "scripts/build_nuitka.ps1", ".env.example",
        "backend/koddi/desktop/web/assets/InterVariable.ttf", "README.md",
    ):
        assert not check(path), path


def test_secret_patterns_detect_values_without_matching_their_definitions():
    secret = AUDIT["SECRET"]
    assert secret.search(b"ghp_" + b"a" * 30)
    assert secret.search(b"sk-" + b"b" * 30)
    assert secret.search(b"-----BEGIN " + b"PRIVATE KEY-----")
    assert not secret.search(Path("scripts/check_repository.py").read_bytes())
