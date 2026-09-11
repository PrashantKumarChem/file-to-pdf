"""Saving, naming, fallback and error handling, with the PDF engine stubbed out."""

import pytest

import converters
import pipeline

FAKE_PDF = b"%PDF-1.7 fake"


@pytest.fixture
def stub_engine(monkeypatch):
    monkeypatch.setattr(converters, "file_to_pdf_bytes", lambda path: FAKE_PDF)


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
    assert result.saved_path.read_bytes() == FAKE_PDF
    assert result.status == f"Done -> {result.saved_path}"


def test_reconverting_a_file_replaces_its_own_pdf(isolated_pipeline, stub_engine, tmp_path):
    src = make(tmp_path / "src", "a.json")
    out = isolated_pipeline["DEFAULT_OUTPUT_DIR"]
    first = pipeline.convert_any(src, out)
    second = pipeline.convert_any(src, out)
    assert second.saved_path == first.saved_path
    assert second.status.startswith("Done, replaced existing PDF -> ")


def test_same_named_files_from_different_folders_do_not_overwrite(
    isolated_pipeline, stub_engine, tmp_path
):
    a = make(tmp_path / "one", "Compound1.json")
    b = make(tmp_path / "two", "Compound1.json")
    out = isolated_pipeline["DEFAULT_OUTPUT_DIR"]
    ra = pipeline.convert_any(a, out)
    rb = pipeline.convert_any(b, out)
    assert ra.saved_path.name == "Compound1.json.pdf"
    assert rb.saved_path.name == "Compound1.json (2).pdf"
    # Converting the second file again keeps using its own numbered PDF.
    assert pipeline.convert_any(b, out).saved_path == rb.saved_path


def test_pdf_left_by_an_earlier_session_is_replaced_and_reported(
    isolated_pipeline, stub_engine, tmp_path
):
    out = isolated_pipeline["DEFAULT_OUTPUT_DIR"]
    out.mkdir()
    (out / "a.json.pdf").write_bytes(b"old")
    result = pipeline.convert_any(make(tmp_path / "src", "a.json"), out)
    assert result.saved_path == out / "a.json.pdf"
    assert "replaced existing PDF" in result.status


def test_falls_back_when_output_dir_refuses_writes(
    isolated_pipeline, stub_engine, monkeypatch, tmp_path
):
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


def test_fails_cleanly_when_fallback_also_refuses(
    isolated_pipeline, stub_engine, monkeypatch, tmp_path
):
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


def test_missing_interpreter_is_reported(isolated_pipeline, monkeypatch, tmp_path):
    monkeypatch.setattr(pipeline, "PYTHON_EXE", str(tmp_path / "no-python.exe"))
    nb = make(tmp_path / "src", "n.ipynb")
    result = pipeline.convert_any(nb, tmp_path / "out")
    assert not result.ok
    assert result.status.startswith("Failed: could not start nbconvert")


def test_log_keeps_one_previous_generation(isolated_pipeline, monkeypatch):
    monkeypatch.setattr(pipeline, "LOG_MAX_BYTES", 50)
    log = isolated_pipeline["LOG_PATH"]
    pipeline._append_log("x" * 100)
    pipeline._append_log("second")
    old = log.with_name("log.old.txt")
    assert "x" * 100 in old.read_text(encoding="utf-8")
    assert log.read_text(encoding="utf-8").startswith("second")
