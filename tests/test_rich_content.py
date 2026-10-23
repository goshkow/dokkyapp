"""Подготовка разметки ответов Докки: чистая функция, без сети и профиля."""

from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

WEB = Path(__file__).resolve().parents[1] / "backend/koddi/desktop/web"


@pytest.fixture(scope="module")
def page():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.route("**/*", lambda route: route.abort())
        page.set_content("<main></main>")
        page.add_script_tag(content=(WEB / "rich-content.js").read_text(encoding="utf-8"))
        yield page
        browser.close()


def prepare(page, text, streaming=False):
    return page.evaluate("([text, streaming]) => richPrepare(text, {streaming})", [text, streaming])


def test_duplicate_arrow_after_link_is_removed(page):
    text = "см. [Кама](https://kama.example.org/a) [↗](https://kama.example.org/a)."
    assert prepare(page, text) == "см. [Кама](https://kama.example.org/a)."


def test_site_photo_placeholder_points_to_attachment_and_is_hidden_while_streaming(page):
    text = "Фото ![Салон](tg://photo?id=media_a)"
    assert prepare(page, text) == "Фото ![Салон](/__rich__/media_a)"
    assert prepare(page, text, True) == "Фото "
    assert prepare(page, "Фото ![Сал", True) == "Фото "
    assert prepare(page, "Фото ![Салон](tg://pho", True) == "Фото "


def test_code_is_not_touched(page):
    text = "```html\n<tg-spoiler>x</tg-spoiler> ![a](tg://photo?id=1)\n```\nи `<hr>`"
    assert prepare(page, text) == text


def test_html_tables_and_figures_become_markdown(page):
    table = prepare(page, "<table><tr><th>A</th><th>B</th></tr><tr><td>1</td><td>2</td></tr></table>")
    assert "| A | B |" in table and "| 1 | 2 |" in table and "<table" not in table
    figure = prepare(page, '<figure><img src="https://x.example.org/a.png"><figcaption>Подпись</figcaption></figure>')
    assert figure == "![Подпись](https://x.example.org/a.png)"


def test_video_audio_and_files_in_image_syntax_become_links(page):
    assert prepare(page, "![Обзор](https://youtu.be/dQw4w9WgXcQ)") == "[Обзор](https://youtu.be/dQw4w9WgXcQ)"
    assert prepare(page, "![Трек](https://x.example.org/a.mp3)") == "[Трек](https://x.example.org/a.mp3)"
    assert prepare(page, "![Файл](https://x.example.org/a.pdf)") == '[Файл](https://x.example.org/a.pdf "rich:file")'
    assert prepare(page, "![Обычная](https://x.example.org/a.jpg)") == "![Обычная](https://x.example.org/a.jpg)"


def test_inline_tags_and_spoilers(page):
    assert prepare(page, "<tg-spoiler>тайна</tg-spoiler> ||ещё||") == (
        '<span class="rich-spoiler">тайна</span> <span class="rich-spoiler">ещё</span>'
    )
    assert prepare(page, "==важно==") == "<mark>важно</mark>"
    assert "<tg-thinking" not in prepare(page, "до <tg-thinking>скрыто</tg-thinking> после")


def test_youtube_ids_and_private_links(page):
    ids = page.evaluate("""() => [richYouTubeId('https://youtu.be/dQw4w9WgXcQ'),
      richYouTubeId('https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=5'), richYouTubeId('https://example.org/watch?v=dQw4w9WgXcQ'),
      richUrl('https://user:pw@example.org/'), richUrl('javascript:alert(1)')]""")
    assert ids == ["dQw4w9WgXcQ", "dQw4w9WgXcQ", "", None, None]


def test_formulas_are_shown_as_escaped_text(page):
    assert prepare(page, "до $$a<b$$ после") == 'до \n\n<div class="rich-math" role="math">a&lt;b</div>\n\n после'
    assert prepare(page, r"\(x^2\)") == '<span class="rich-math-inline" role="math">x^2</span>'
    # цены с долларом не принимаются за формулу
    assert prepare(page, "стоит $5 и $10") == "стоит $5 и $10"
