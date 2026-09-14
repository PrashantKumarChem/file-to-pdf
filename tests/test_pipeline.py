"""Saving, naming, fallback and error handling, with the PDF engine stubbed out."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

from topdf import converters, pipeline

FAKE_PDF = b"%PDF-1.7 fake"
REPO = Path(__file__).resolve().parents[1]


@pytest.fixture
def stub_engine(monkeypatch):
    monkeypatch.setattr(converters, "render", lambda path: converters.Rendered(FAKE_PDF, []))


def make(folder, name, text="{}"):
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    path.write_text(text, encoding="utf-8")
    return path


def test_saves_into_output_dir(isolated_pipeline, stub_engine, tmp_path):
    src = make(tmp_path / "src", "a.json")
    result = pipeline.convert_any(src, isolated_pipeline["DEFAULT_OUTPUT_DIR"])
    assert result.ok
    assert result.saved_path == isolated_pipeline["DEFAULT_OUTPUT_DIR"] / "a.json.pdf"
    assert result.saved_path is not None
    assert result.saved_path.read_bytes() == FAKE_PDF
    assert result.status == f"Done -> {result.saved_path}"


def test_notebook_pdf_is_named_after_the_notebook(isolated_pipeline, stub_engine, tmp_path):
    out = isolated_pipeline["DEFAULT_OUTPUT_DIR"]
    notebook = pipeline.convert_any(make(tmp_path / "src", "analysis.ipynb"), out)
    data = pipeline.convert_any(make(tmp_path / "src", "analysis.json"), out)
    assert notebook.saved_path is not None and data.saved_path is not None
    assert (notebook.saved_path.name, data.saved_path.name) == ("analysis.pdf", "analysis.json.pdf")


def test_reconverting_a_file_replaces_its_own_pdf(isolated_pipeline, stub_engine, tmp_path):
    src = make(tmp_path / "src", "a.json")
    out = isolated_pipeline["DEFAULT_OUTPUT_DIR"]
    first = pipeline.convert_any(src, out)
    second = pipeline.convert_any(src, out)
    assert second.saved_path == first.saved_path
    assert second.status.startswith("Done, replaced existing PDF -> ")


def test_same_named_files_from_different_folders_do_not_overwrite(isolated_pipeline, stub_engine, tmp_path):
    a = make(tmp_path / "one", "analysis.json")
    b = make(tmp_path / "two", "analysis.json")
    out = isolated_pipeline["DEFAULT_OUTPUT_DIR"]
    ra = pipeline.convert_any(a, out)
    rb = pipeline.convert_any(b, out)
    assert ra.saved_path is not None and rb.saved_path is not None
    assert ra.saved_path.name == "analysis.json.pdf"
    assert rb.saved_path.name == "analysis.json (2).pdf"
    # Converting the second file again keeps using its own numbered PDF.
    assert pipeline.convert_any(b, out).saved_path == rb.saved_path


def test_pdf_left_by_an_earlier_session_is_replaced_and_reported(isolated_pipeline, stub_engine, tmp_path):
    out = isolated_pipeline["DEFAULT_OUTPUT_DIR"]
    out.mkdir()
    (out / "a.json.pdf").write_bytes(b"old")
    result = pipeline.convert_any(make(tmp_path / "src", "a.json"), out)
    assert result.saved_path == out / "a.json.pdf"
    assert "replaced existing PDF" in result.status


def test_web_resources_that_did_not_load_are_a_warning(isolated_pipeline, monkeypatch, tmp_path):
    url = "https://cdn.jsdelivr.net/npm/vega@5"
    monkeypatch.setattr(converters, "render", lambda path: converters.Rendered(FAKE_PDF, [url]))
    result = pipeline.convert_any(make(tmp_path / "src", "chart.ipynb"), isolated_pipeline["DEFAULT_OUTPUT_DIR"])
    assert result.ok
    assert result.saved_path is not None
    assert result.saved_path.read_bytes() == FAKE_PDF
    assert result.status == (
        f"Warning: 1 web resource did not load; charts or math may be missing. Done -> {result.saved_path}"
    )
    assert url in isolated_pipeline["LOG_PATH"].read_text(encoding="utf-8")


def test_falls_back_when_output_dir_refuses_writes(isolated_pipeline, stub_engine, monkeypatch, tmp_path):
    blocked = tmp_path / "blocked"
    real_write = pipeline._write_pdf

    def write(dest, data, source):
        if dest.parent == blocked:
            raise PermissionError("refused")
        real_write(dest, data, source)

    monkeypatch.setattr(pipeline, "_write_pdf", write)
    result = pipeline.convert_any(make(tmp_path / "src", "a.json"), blocked)
    assert result.ok
    assert result.saved_path == isolated_pipeline["FALLBACK_DIR"] / "a.json.pdf"
    assert result.status.startswith("Saved to fallback")


def test_fails_cleanly_when_fallback_also_refuses(isolated_pipeline, stub_engine, monkeypatch, tmp_path):
    def refuse(dest, data, source):
        raise PermissionError("refused")

    monkeypatch.setattr(pipeline, "_write_pdf", refuse)
    result = pipeline.convert_any(make(tmp_path / "src", "a.json"), tmp_path / "out")
    assert not result.ok
    assert result.saved_path is None
    assert "fallback folder" in result.status


def test_convert_any_never_raises(isolated_pipeline, monkeypatch, tmp_path):
    def explode(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(pipeline, "convert_file", explode)
    result = pipeline.convert_any(make(tmp_path / "src", "a.json"), tmp_path / "out")
    assert not result.ok
    assert result.status == "Failed: boom"
    assert "Traceback" in result.log
    assert "RuntimeError: boom" in isolated_pipeline["LOG_PATH"].read_text(encoding="utf-8")


def test_binary_input_is_a_failed_result(isolated_pipeline, tmp_path):
    src = tmp_path / "blob.bin"
    src.write_bytes(bytes(range(256)))
    result = pipeline.convert_any(src, tmp_path / "out")
    assert not result.ok
    assert result.status == "Failed: blob.bin is a binary file, not text"


def test_unreadable_notebook_is_a_failed_result(isolated_pipeline, tmp_path):
    result = pipeline.convert_any(make(tmp_path / "src", "broken.ipynb", "not json"), tmp_path / "out")
    assert not result.ok
    assert result.status.startswith("Failed: ")
    assert "CONVERSION ERROR" in result.log


def test_log_keeps_one_previous_generation(isolated_pipeline, monkeypatch):
    monkeypatch.setattr(pipeline, "LOG_MAX_BYTES", 50)
    log = isolated_pipeline["LOG_PATH"]
    pipeline._append_log("x" * 100)
    pipeline._append_log("second")
    old = log.with_name("log.old.txt")
    assert "x" * 100 in old.read_text(encoding="utf-8")
    assert log.read_text(encoding="utf-8").startswith("second")


def test_log_folder_is_created_when_missing(isolated_pipeline, monkeypatch, tmp_path):
    log = tmp_path / "LocalAppData" / "File to PDF" / "conversion_log.txt"
    monkeypatch.setattr(pipeline, "LOG_PATH", log)
    pipeline._append_log("first entry")
    assert log.read_text(encoding="utf-8").startswith("first entry")


def test_log_lives_in_local_app_data(tmp_path):
    # Module-level paths are fixed at import, so check them in a fresh process.
    code = "from topdf import pipeline; print(pipeline.LOG_PATH)"
    env = {**os.environ, "LOCALAPPDATA": str(tmp_path)}
    out = subprocess.run(
        [sys.executable, "-c", code], cwd=REPO, env=env, capture_output=True, text=True, check=True
    ).stdout.strip()
    assert Path(out) == tmp_path / "File to PDF" / "conversion_log.txt"
