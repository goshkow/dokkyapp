from __future__ import annotations

import asyncio
import base64
import io

import pytest
from PIL import Image

import koddi.attachments.recognize as recognize
import koddi.attachments.service as attachments
import koddi.storage.db as storage


@pytest.fixture(autouse=True)
def profile(tmp_path, monkeypatch):
    monkeypatch.setenv("KODDI_DATA_DIR", str(tmp_path / "profile"))
    monkeypatch.setenv("KODDI_INTERNAL_TEST", "0")
    monkeypatch.setattr(storage, "DB_PATH", tmp_path / "test.sqlite3")
    storage.init_db()
    storage.set_setting("dokky_user", {"id": "alice"})
    from koddi.dokky.client import dokky

    monkeypatch.setattr(dokky.store, "load", lambda: {"access_token": "fixture-session"})
    return tmp_path


def chat(owner="alice"):
    with storage.db() as conn:
        return conn.execute("INSERT INTO chats(owner_id) VALUES (?)", (owner,)).lastrowid


def upload(chat_id, name, content):
    return attachments.store_attachment(chat_id, name, io.BytesIO(content))


def png(width=3000, height=2000, mode="RGBA"):
    buffer = io.BytesIO()
    image = Image.effect_noise((width, height), 60).convert(mode)
    image.save(buffer, "PNG")
    return buffer.getvalue()


class Vision:
    def __init__(self, answer="на картинке кнопка «Отмена»", fail=False):
        self.answer, self.fail, self.calls = answer, fail, []

    async def stream(self, *, model, messages, tools, options=None):
        self.calls.append({"model": model, "messages": messages, "tools": tools})
        if self.fail:
            raise RuntimeError("модель без зрения")
        yield {"content": self.answer}


def run(coroutine):
    return asyncio.run(coroutine)


def test_large_image_is_shrunk_to_a_jpeg_that_fits_the_request_budget():
    item = upload(chat(), "screen.png", png())
    url = recognize.image_url(item)
    assert url.startswith("data:image/jpeg;base64,")
    encoded = url.split(",", 1)[1]
    assert len(encoded) <= recognize.IMAGE_CHARS
    with Image.open(io.BytesIO(base64.b64decode(encoded))) as image:
        assert image.format == "JPEG" and max(image.size) <= 1568


def test_oversized_image_header_is_rejected_before_decoding(monkeypatch):
    item = upload(chat(), "huge.png", png(400, 300))
    monkeypatch.setattr(recognize, "MAX_IMAGE_PIXELS", 1000)
    with pytest.raises(ValueError):
        recognize.image_url(item)


def test_images_are_recognized_upfront_and_documents_are_listed_for_attachment_read():
    own = chat()
    note = upload(own, "план.md", "# План\nсделать отмену".encode())
    shot = upload(own, "shot.png", png(300, 200))
    vision = Vision()
    block = run(recognize.attachment_context(vision, "dokky-code", own, [note["id"], shot["id"]]))
    assert block.startswith(recognize.HEADER)
    assert block.index(f"[ВЛОЖЕНИЕ: план.md] attachment_id={note['id']}") < block.index("[АНАЛИЗ И ОРИГИНАЛЬНЫЙ ТЕКСТ ИЗОБРАЖЕНИЯ: shot.png]")
    # текст документа агент читает сам через attachment.read, картинка - уже распознана
    assert "attachment.read" in block and "сделать отмену" not in block and "кнопка «Отмена»" in block
    # картинка ушла отдельным запросом без истории и инструментов, в историю чата идёт только текст
    assert len(vision.calls) == 1
    call = vision.calls[0]
    assert call["tools"] == [] and call["messages"][0]["role"] == "system"
    parts = call["messages"][1]["content"]
    assert parts[1]["type"] == "image_url" and parts[1]["image_url"]["url"].startswith("data:image/jpeg;base64,")
    assert "base64" not in block
    again = run(recognize.attachment_context(vision, "dokky-code", own, [shot["id"]]))
    assert "кнопка «Отмена»" in again and len(vision.calls) == 1


def test_chat_mode_without_tools_gets_document_text_inline():
    own = chat()
    note = upload(own, "data.csv", "имя;сумма\nАнна;10".encode())
    block = run(recognize.attachment_context(Vision(), "m", own, [note["id"]], read_tool=False))
    assert "[ТЕКУЩЕЕ ВЛОЖЕНИЕ: data.csv]" in block and "Анна;10" in block


def test_failed_recognition_and_unsupported_types_do_not_break_the_turn():
    own = chat()
    shot = upload(own, "shot.png", png(200, 100))
    pdf = upload(own, "отчёт.pdf", b"%PDF-1.4 test")
    block = run(recognize.attachment_context(Vision(fail=True), "m", own, [shot["id"], pdf["id"]]))
    assert "[ВЛОЖЕНИЕ: shot.png]\nНе удалось прочитать" in block
    assert "[ВЛОЖЕНИЕ: отчёт.pdf]\nСодержимое файлов этого типа не извлекается" in block
    assert recognize.recognized_text(attachments.require_attachment(shot["id"])) is None


def test_attachments_of_another_chat_are_not_included():
    own, other = chat(), chat()
    foreign = upload(other, "secret.md", b"secret")
    vision = Vision()
    assert run(recognize.attachment_context(vision, "m", own, [foreign["id"], 999_999])) == ""
    assert vision.calls == []


def test_reading_an_image_attachment_returns_its_recognition_instead_of_an_error():
    own = chat()
    shot = upload(own, "shot.png", png(200, 100))
    unread = attachments.read_attachment(shot["id"], own)
    assert unread["ok"] is True and unread["text"] == "" and "not recognized" in unread["note"]
    run(recognize.attachment_context(Vision(), "m", own, [shot["id"]]))
    read = attachments.read_attachment(shot["id"], own)
    assert read["text"] == "на картинке кнопка «Отмена»"


def test_recognition_is_not_reused_for_another_instruction_version(monkeypatch):
    own = chat()
    shot = upload(own, "shot.png", png(200, 100))
    vision = Vision()
    run(recognize.attachment_context(vision, "m", own, [shot["id"]]))
    monkeypatch.setattr(recognize, "VERSION", recognize.VERSION + 1)
    run(recognize.attachment_context(vision, "m", own, [shot["id"]]))
    assert len(vision.calls) == 2


def test_turn_sends_recognized_attachments_to_the_model_but_shows_the_plain_message():
    from koddi.agents.orchestrator import AgentOrchestrator

    own = chat()
    shot = upload(own, "shot.png", png(300, 200))

    class Model:
        config = {"provider": "dokky-test", "model": "mock"}

        def __init__(self):
            self.requests = []

        async def stream(self, *, messages, tools, **kwargs):
            self.requests.append(messages)
            if isinstance(messages[-1]["content"], list):
                yield {"content": "кнопка «Отмена» пропала"}
            else:
                yield {"content": "Готово", "finish_reason": "stop"}

    model = Model()

    async def turn():
        return [event async for event in AgentOrchestrator(model).run(
            chat_id=own, project_id=None, workspace=None, user_message="почини отмену",
            attachment_ids=[shot["id"]], mode="chat",
        )]

    events = run(turn())
    assert any("Распознаю вложения" in event for event in events)
    vision, *answers = model.requests
    assert vision[1]["content"][1]["type"] == "image_url"
    user = [message for message in answers[0] if message.get("role") == "user"][-1]["content"]
    assert user.startswith("почини отмену" + recognize.HEADER)
    assert "кнопка «Отмена» пропала" in user and "base64" not in user
    with storage.db() as conn:
        assert conn.execute("SELECT content FROM messages WHERE role='user'").fetchone()[0] == "почини отмену"
