"""PDF читает отдельный процесс: текст, скан, пароль, битый файл, зависание."""
from __future__ import annotations

import io
import os
import subprocess
import sys
from pathlib import Path

import pytest
from pypdf import PdfWriter

from koddi.documents import pdf_worker
from koddi.documents.service import READABLE_SUFFIXES, document_limit, extract_text
from koddi.documents.pdf_worker import PdfUnreadable, pdf_text


def text_pdf(words: str = "Hello from Dokky") -> bytes:
    stream = f"BT /F1 24 Tf 72 720 Td ({words}) Tj ET".encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n%s\nendstream" % (len(stream), stream),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = io.BytesIO(b"%PDF-1.4\n")
    out.seek(0, 2)
    offsets = []
    for number, body in enumerate(objects, 1):
        offsets.append(out.tell())
        out.write(b"%d 0 obj\n%s\nendobj\n" % (number, body))
    start = out.tell()
    out.write(b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1))
    for offset in offsets:
        out.write(b"%010d 00000 n \n" % offset)
    out.write(b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, start))
    return out.getvalue()


def blank_pdf() -> bytes:
    writer = PdfWriter()
    writer.add_blank_page(100, 100)
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def locked_pdf() -> bytes:
    writer = PdfWriter()
    writer.add_blank_page(100, 100)
    writer.encrypt("secret", algorithm="RC4-128")
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def test_pdf_text_is_read_in_a_separate_process() -> None:
    assert "Hello from Dokky" in pdf_text(text_pdf())
    text, _outline = extract_text(Path("report.PDF"), source_bytes=text_pdf())
    assert "Hello from Dokky" in text


@pytest.mark.parametrize(
    "make,reason",
    [(blank_pdf, "скан"), (locked_pdf, "паролем"), (lambda: b"not a pdf at all", "повреждён")],
)
def test_unreadable_pdf_is_explained(make, reason) -> None:
    with pytest.raises(PdfUnreadable, match=reason):
        pdf_text(make())


def test_hung_worker_is_stopped(monkeypatch) -> None:
    def hang(*_args, **kwargs):
        raise subprocess.TimeoutExpired("pdf", kwargs["timeout"])

    monkeypatch.setattr(pdf_worker.subprocess, "run", hang)
    with pytest.raises(PdfUnreadable, match="долго"):
        pdf_text(text_pdf())


def test_crashed_worker_is_not_a_crash_of_the_app(monkeypatch) -> None:
    monkeypatch.setattr(
        pdf_worker.subprocess, "run", lambda *_a, **_k: subprocess.CompletedProcess([], -9, stdout=b"", stderr=b""),
    )
    with pytest.raises(PdfUnreadable, match="повреждён"):
        pdf_text(text_pdf())


def test_worker_is_stopped_when_memory_runs_out(monkeypatch) -> None:
    if os.name == "nt":
        pytest.skip("на Windows лимита памяти нет")
    code = (
        "from koddi.documents import pdf_worker as w\n"
        "w._MEMORY_BYTES = 200 * 1024 * 1024\n"
        "w._limit_resources()\n"
        "import time\n"
        "kept = bytearray(400 * 1024 * 1024)\n"
        "kept[::4096] = b'x' * len(kept[::4096])\n"
        "time.sleep(5)\n"
    )
    env = {"PYTHONPATH": str(Path(pdf_worker.__file__).resolve().parents[2])}
    assert subprocess.run([sys.executable, "-c", code], env=env, timeout=20, check=False).returncode == 3


def test_worker_does_not_inherit_app_secrets(monkeypatch) -> None:
    monkeypatch.setenv("DOKKY_TOKEN", "secret")
    _command, env = pdf_worker._command()
    assert "DOKKY_TOKEN" not in env


def test_limits() -> None:
    assert ".pdf" in READABLE_SUFFIXES
    assert document_limit("a.pdf") > document_limit("a.txt")
    with pytest.raises(PdfUnreadable, match="64"):
        pdf_text(b"0" * (pdf_worker.MAX_PDF_BYTES + 1))
    assert pdf_worker.read(text_pdf(), max_chars=5)["text"] == "Hello"


def test_section_for_chat_mode_gives_the_reason(monkeypatch) -> None:
    from koddi.attachments import recognize

    monkeypatch.setattr(recognize, "_read_attachment_bytes", lambda _item, _limit: blank_pdf())
    with pytest.raises(PdfUnreadable):
        recognize.document_text({"original_name": "scan.pdf"})
