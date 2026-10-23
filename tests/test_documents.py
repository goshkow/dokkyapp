from pathlib import Path

from koddi.documents.service import extract_text


def test_html_is_normalized_before_indexing(tmp_path: Path) -> None:
    html = tmp_path / "page.html"
    html.write_text(
        "<html><head><script>secret()</script></head><body><h1>Hello</h1><p>World</p></body></html>",
        encoding="utf-8",
    )
    text, _outline = extract_text(html)
    assert "Hello" in text and "World" in text
    assert "<html" not in text
    assert "secret" not in text
