from koddi.i18n import check_locales
from koddi.tools.runner import ToolRunner


def test_locales_are_symmetric_and_utf8() -> None:
    result = check_locales()
    assert result["ok"] is True
    assert result["missing_ru"] == []
    assert result["missing_en"] == []
    assert result["mojibake"] == []


def test_i18n_tool_runs_real_checker() -> None:
    result = ToolRunner(workspace=None).run("app.i18n_check", {})
    assert result["ok"] is True
    assert "locales" in result
