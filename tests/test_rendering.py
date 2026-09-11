"""End-to-end rendering through Chromium and nbconvert. Slower: a few seconds each."""

import base64
import json
import threading

import pytest

fitz = pytest.importorskip("fitz")  # PyMuPDF, from requirements-dev.txt
pytest.importorskip("playwright")

import converters  # noqa: E402
import pipeline  # noqa: E402

pytestmark = pytest.mark.slow

PNG_8X8 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAgAAAAICAIAAABLbSncAAAAEklEQVR4nGP4z8CAFWEXHbQSACj/P8Fu7N9hAAAAAElFTkSuQmCC"
)


def pdf_doc(data: bytes):
    return fitz.open(stream=data, filetype="pdf")


def test_letter_pages_and_no_threads_left_behind():
    before = threading.active_count()
    doc = pdf_doc(converters.html_to_pdf(converters.wrap_html("t", "Text", "<p>hello</p>")))
    page = doc[0]
    assert (round(page.rect.width), round(page.rect.height)) == (612, 792)
    assert "hello" in page.get_text()
    assert threading.active_count() == before


def test_render_shuts_down_its_thread_pool_and_event_loop(monkeypatch):
    # Counting threads can't show this: an abandoned pool is often collected
    # before the count is taken. Check the cleanup calls themselves.
    shutdowns, loops = [], []
    real_shutdown = converters.concurrent.futures.ThreadPoolExecutor.shutdown
    real_new_loop = converters._new_event_loop

    def recording_shutdown(self, *args, **kwargs):
        shutdowns.append(True)
        return real_shutdown(self, *args, **kwargs)

    def recording_new_loop():
        loop = real_new_loop()
        loops.append(loop)
        return loop

    monkeypatch.setattr(converters.concurrent.futures.ThreadPoolExecutor, "shutdown", recording_shutdown)
    monkeypatch.setattr(converters, "_new_event_loop", recording_new_loop)
    converters.html_to_pdf("<p>x</p>")
    assert shutdowns
    assert len(loops) == 1 and loops[0].is_closed()


def test_browser_is_closed_when_printing_fails(monkeypatch):
    from playwright.async_api._generated import Browser, Page

    async def failing_pdf(self, *args, **kwargs):
        raise RuntimeError("pdf failed")

    closed = []
    real_close = Browser.close

    async def recording_close(self, *args, **kwargs):
        closed.append(True)
        return await real_close(self, *args, **kwargs)

    monkeypatch.setattr(Page, "pdf", failing_pdf)
    monkeypatch.setattr(Browser, "close", recording_close)
    before = threading.active_count()
    with pytest.raises(RuntimeError, match="pdf failed"):
        converters.html_to_pdf("<p>x</p>")
    assert closed == [True]
    assert threading.active_count() == before


def test_markdown_image_resolves_next_to_the_source(tmp_path):
    folder = tmp_path / "notes with spaces"
    folder.mkdir()
    (folder / "dot.png").write_bytes(PNG_8X8)
    md = folder / "notes.md"
    md.write_text("# Notes\n\n![dot](dot.png)\n", encoding="utf-8")
    page = pdf_doc(converters.file_to_pdf_bytes(md))[0]
    assert [(img[2], img[3]) for img in page.get_images(full=True)] == [(8, 8)]


def test_crlf_text_lays_out_like_lf_text(tmp_path):
    lines = [f"line {i:04d} " + "x" * (i % 90) for i in range(300)]
    lf, crlf = tmp_path / "lf.txt", tmp_path / "crlf.txt"
    lf.write_bytes("\n".join(lines).encode())
    crlf.write_bytes("\r\n".join(lines).encode())
    assert len(pdf_doc(converters.file_to_pdf_bytes(crlf))) == len(
        pdf_doc(converters.file_to_pdf_bytes(lf))
    )


def test_notebook_converts_with_the_bundled_template(isolated_pipeline, tmp_path):
    long_line = "value = '" + "wrap me " * 40 + "'"
    nb = {
        "cells": [{
            "cell_type": "code", "execution_count": 1, "metadata": {},
            "source": [long_line + "\n", "print('done')"],
            "outputs": [{"name": "stdout", "output_type": "stream", "text": ["done\n"]}],
        }],
        "metadata": {"kernelspec": {"name": "python3", "display_name": "Python 3", "language": "python"}},
        "nbformat": 4, "nbformat_minor": 5,
    }
    src = tmp_path / "sample.ipynb"
    src.write_text(json.dumps(nb), encoding="utf-8")
    result = pipeline.convert_notebook(src, isolated_pipeline["DEFAULT_OUTPUT_DIR"])
    assert result.ok, result.log
    text = " ".join(pdf_doc(result.saved_path.read_bytes())[0].get_text().split())
    # pdf-nowrap-fix wraps the long line instead of clipping it at the page
    # edge, and PyMuPDF only extracts text inside the page.
    assert text.count("wrap me") == 40
