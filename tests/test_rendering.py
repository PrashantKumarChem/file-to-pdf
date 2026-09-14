"""End-to-end rendering through Chromium and nbconvert. Slower: a few seconds each."""

import base64
import json
import socket
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


def write_notebook(path, cells):
    path.write_text(json.dumps({
        "cells": cells,
        "metadata": {"kernelspec": {"name": "python3", "display_name": "Python 3", "language": "python"}},
        "nbformat": 4, "nbformat_minor": 5,
    }), encoding="utf-8")
    return path


def test_letter_pages_and_no_threads_left_behind():
    before = threading.active_count()
    doc = pdf_doc(converters.html_to_pdf(converters.wrap_html("<p>hello</p>")))
    page = doc[0]
    assert (round(page.rect.width), round(page.rect.height)) == (612, 792)
    assert "hello" in page.get_text()
    assert threading.active_count() == before


def test_json_pdf_is_raw_json_in_the_paper_palette(tmp_path):
    f = tmp_path / "Compound1.json"
    f.write_text('{\n  "name": "null inside",\n  "mass": 1.0,\n  "ok": true\n}\n', encoding="utf-8")
    page = pdf_doc(converters.file_to_pdf_bytes(f))[0]
    spans = [
        (s["text"], s["color"], "Bold" in s["font"])
        for block in page.get_text("dict")["blocks"]
        for line in block.get("lines", [])
        for s in line["spans"]
        if s["text"].strip()
    ]
    assert spans[0] == ("{", 0x1D1D1F, False)  # no heading above the JSON
    assert ('"name"', 0x9B2158, True) in spans
    assert ('"null inside"', 0x0F7D33, False) in spans
    assert ("true", 0x7A3FC4, False) in spans
    assert any(text.startswith(": 1.0") and color == 0x1D1D1F for text, color, _ in spans)
    left = min(w[0] for w in page.get_text("words"))
    assert 42 < left < 45  # 0.6in = 43.2pt


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


def test_local_folder_paths_stay_out_of_the_pdf(tmp_path):
    folder = tmp_path / "Private Folder Name"
    folder.mkdir()
    (folder / "dot.png").write_bytes(PNG_8X8)
    md = folder / "notes.md"
    md.write_text(
        "# Notes\n\n![dot](dot.png)\n\n[sibling](other.md), [absolute](file:///C:/Windows/win.ini),"
        " [web](https://example.com) and [jump](#target)\n\n" + "filler\n\n" * 150
        + '<h2 id="target">Target</h2>\n',
        encoding="utf-8",
    )
    data = converters.file_to_pdf_bytes(md)
    doc = pdf_doc(data)
    page = doc[0]
    links = page.get_links()
    # Local file links become plain text; web links stay clickable; the in-page
    # anchor stays an internal jump to the heading's page.
    assert [link.get("uri") for link in links if link["kind"] == fitz.LINK_URI] == ["https://example.com/"]
    assert [link.get("page") for link in links if link["kind"] != fitz.LINK_URI] == [len(doc) - 1]
    assert "sibling" in page.get_text()
    for fragment in (b"Private Folder Name", b"Private%20Folder%20Name", tmp_path.name.encode()):
        assert fragment not in data
    assert len(page.get_images()) == 1


def test_crlf_text_lays_out_like_lf_text(tmp_path):
    lines = [f"line {i:04d} " + "x" * (i % 90) for i in range(300)]
    lf, crlf = tmp_path / "lf.txt", tmp_path / "crlf.txt"
    lf.write_bytes("\n".join(lines).encode())
    crlf.write_bytes("\r\n".join(lines).encode())
    assert len(pdf_doc(converters.file_to_pdf_bytes(crlf))) == len(
        pdf_doc(converters.file_to_pdf_bytes(lf))
    )


def test_failed_web_resources_are_reported(tmp_path):
    with socket.socket() as s:  # a port nothing listens on once closed
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    url = f"http://127.0.0.1:{port}/figure.png"
    md = tmp_path / "remote.md"
    md.write_text(f"# Figure\n\n![figure]({url})\n", encoding="utf-8")
    rendered = converters.render(md)
    assert rendered.failed_requests == [url]
    assert "Figure" in pdf_doc(rendered.pdf)[0].get_text()


def test_notebook_converts_with_the_bundled_template(isolated_pipeline, tmp_path):
    long_line = "value = '" + "wrap me " * 40 + "'"
    src = write_notebook(tmp_path / "sample.ipynb", [{
        "id": "c1", "cell_type": "code", "execution_count": 1, "metadata": {},
        "source": [long_line + "\n", "print('done')"],
        "outputs": [{"name": "stdout", "output_type": "stream", "text": ["done\n"]}],
    }])
    result = pipeline.convert_any(src, isolated_pipeline["DEFAULT_OUTPUT_DIR"])
    assert result.ok, result.log
    assert result.saved_path.name == "sample.pdf"
    doc = pdf_doc(result.saved_path.read_bytes())
    assert doc.metadata["title"] == "sample"
    text = " ".join(doc[0].get_text().split())
    # pdf-nowrap-fix wraps the long line instead of clipping it at the page
    # edge, and PyMuPDF only extracts text inside the page.
    assert text.count("wrap me") == 40


def test_notebook_links_to_local_files_keep_their_look_but_not_their_path(tmp_path):
    folder = tmp_path / "Private Folder Name"
    folder.mkdir()
    (folder / "data.csv").write_text("a,b\n1,2\n", encoding="utf-8")
    src = write_notebook(folder / "links.ipynb", [{
        "id": "m1", "cell_type": "markdown", "metadata": {},
        "source": "See [sibling](data.csv) and [web](https://example.com).",
    }])
    data = converters.file_to_pdf_bytes(src)
    for fragment in (b"Private Folder Name", b"Private%20Folder%20Name", tmp_path.name.encode(), b"file:"):
        assert fragment not in data
    page = pdf_doc(data)[0]
    assert [link.get("uri") for link in page.get_links()] == ["https://example.com/"]
    colors = {
        s["text"].strip(): s["color"]
        for block in page.get_text("dict")["blocks"]
        for line in block.get("lines", [])
        for s in line["spans"]
    }
    # The local link lost its target but still prints in the link color, as
    # notebook PDFs always showed it.
    assert colors["sibling"] == colors["web"] != colors["See"]
