"""The command line: path expansion, output folders, exit codes. PDF engine stubbed."""

import pytest

import converters
import notebook_to_pdf_cli as cli
import pipeline

FAKE_PDF = b"%PDF-1.7 fake"
PNG_HEAD = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"


@pytest.fixture
def stub_engine(monkeypatch):
    def render(path):
        if path.name.startswith("broken"):
            raise ValueError("cannot print this")
        return converters.Rendered(FAKE_PDF, [])

    monkeypatch.setattr(converters, "render", render)


def write(path, text="{}"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


@pytest.fixture
def folder(tmp_path):
    root = tmp_path / "SI files"
    for name in ("Compound1.ipynb", "Compound1.json", "notes.md", "fit.py", "run.log"):
        write(root / name)
    write(root / "geometry.xyz", "3\nwater\nO 0 0 0.117\n")  # text, no highlighter for .xyz
    (root / "spectrum.png").write_bytes(PNG_HEAD)
    (root / "Compound1.pdf").write_bytes(FAKE_PDF)  # an earlier run's output
    write(root / ".ipynb_checkpoints" / "Compound1-checkpoint.ipynb")
    write(root / "raw" / "Compound2.json")
    return root


def names(files):
    return [p.name for p in files]


def test_folder_contributes_the_files_that_print_in_name_order(folder):
    files, missing, skipped = cli.collect([str(folder)])
    assert names(files) == ["Compound1.ipynb", "Compound1.json", "fit.py", "geometry.xyz", "notes.md", "run.log"]
    assert names(skipped) == ["Compound1.pdf", "spectrum.png"]
    assert missing == []


def test_recursive_folder_skips_hidden_folders(folder):
    files, _, _ = cli.collect([str(folder)], recursive=True)
    assert "Compound2.json" in names(files)
    assert "Compound1-checkpoint.ipynb" not in names(files)


def test_named_files_convert_whatever_their_type(folder):
    files, _, skipped = cli.collect([str(folder / "spectrum.png")])
    assert names(files) == ["spectrum.png"]
    assert skipped == []


def test_wildcards_expand_and_misses_are_reported(folder):
    files, missing, _ = cli.collect([str(folder / "*.json"), str(folder / "*.xlsx"), str(folder / "gone.md")])
    assert names(files) == ["Compound1.json"]
    assert missing == [str(folder / "*.xlsx"), str(folder / "gone.md")]


def test_a_file_given_twice_converts_once(folder):
    one = folder / "notes.md"
    files, _, _ = cli.collect([str(one), str(folder / "*.md"), str(one).upper()])
    assert files == [one]


def test_converts_into_the_default_folder(isolated_pipeline, stub_engine, folder, capsys):
    assert cli.main([str(folder / "Compound1.ipynb"), str(folder / "Compound1.json")]) == 0
    out = isolated_pipeline["DEFAULT_OUTPUT_DIR"]
    assert sorted(p.name for p in out.iterdir()) == ["Compound1.json.pdf", "Compound1.pdf"]
    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == f"[1/2] Compound1.ipynb: Done -> {out / 'Compound1.pdf'}"
    assert lines[-1] == "2 of 2 converted"


def test_skipped_folder_files_are_reported(isolated_pipeline, stub_engine, folder, capsys):
    assert cli.main([str(folder)]) == 0
    captured = capsys.readouterr()
    assert "Skipped 2 files (PDF or not text): Compound1.pdf, spectrum.png" in captured.err
    assert captured.out.splitlines()[-1] == "6 of 6 converted"


def test_out_and_next_to_source(isolated_pipeline, stub_engine, folder, tmp_path):
    assert cli.main([str(folder / "notes.md"), "--out", str(tmp_path / "pdfs")]) == 0
    assert (tmp_path / "pdfs" / "notes.md.pdf").read_bytes() == FAKE_PDF
    assert cli.main([str(folder / "raw"), "--next-to-source"]) == 0
    assert (folder / "raw" / "Compound2.json.pdf").exists()


def test_out_and_next_to_source_are_exclusive(folder):
    with pytest.raises(SystemExit):
        cli.main([str(folder), "--out", "x", "--next-to-source"])


def test_a_failure_does_not_stop_the_batch_and_sets_the_exit_code(isolated_pipeline, stub_engine, tmp_path, capsys):
    broken = write(tmp_path / "broken.json")
    good = write(tmp_path / "good.json")
    assert cli.main([str(broken), str(good)]) == 1
    out = capsys.readouterr().out
    assert "[1/2] broken.json: Failed: cannot print this" in out
    assert "[2/2] good.json: Done -> " in out
    assert f"1 of 2 converted, 1 failed (details in {pipeline.LOG_PATH})" in out


def test_nothing_to_convert(isolated_pipeline, stub_engine, tmp_path, capsys):
    assert cli.main([str(tmp_path / "missing.json")]) == 2
    assert "Not found:" in capsys.readouterr().err
