"""topdf.exe from the unzipped app on every kind of file the corpus holds."""

import shutil
import tomllib

from conftest import CORPUS, REPO, pdf_text, run_topdf

# Corpus file -> the PDF it becomes, and text that must be in it.
EXPECTED = {
    "data.json": ("data.json.pdf", '"esterification-screen"'),
    "fit.py": ("fit.py.pdf", "def rate_constant"),
    "geometry.xyz": ("geometry.xyz.pdf", "water, synthetic"),
    "legacy-cp1252.txt": ("legacy-cp1252.txt.pdf", "Café"),
    "notebook.ipynb": ("notebook.pdf", "Esterification yield summary"),
    "notes.md": ("notes.md.pdf", "Lab notes"),
    "pump.yaml": ("pump.yaml.pdf", "rate_ml_min"),
    "run.log": ("run.log.pdf", "step 110 of 110"),
    "settings.ini": ("settings.ini.pdf", "wavelength_nm"),
    "spectrum-utf16.txt": ("spectrum-utf16.txt.pdf", "Absorbance"),
    "Überprüfung.ipynb": ("Überprüfung.pdf", "in Ordnung"),
}


def test_the_corpus_lists_every_file_the_test_expects():
    assert sorted(p.name for p in CORPUS.iterdir() if p.is_file()) == sorted(EXPECTED)


def test_every_file_type_converts(app, sandbox):
    sources = sandbox.root / "sources"
    shutil.copytree(CORPUS, sources)
    (sources / "spectrum.bin").write_bytes(bytes(range(256)))
    out = sandbox.root / "out"

    run = run_topdf(app, sandbox, sources, "--out", out)

    assert run.returncode == 0
    assert "Skipped 1 file (PDF or not text): spectrum.bin" in run.stderr
    assert f"{len(EXPECTED)} of {len(EXPECTED)} converted" in run.stdout
    assert sorted(p.name for p in out.glob("*.pdf")) == sorted(pdf for pdf, _ in EXPECTED.values())
    for pdf, text in EXPECTED.values():
        content, pages = pdf_text(out / pdf)
        assert pages >= 1, pdf
        assert text in content, pdf
    assert sandbox.log.is_file()


def test_a_binary_file_named_directly_fails_with_the_reason(app, sandbox):
    blob = sandbox.root / "spectrum.bin"
    blob.write_bytes(bytes(range(256)))
    out = sandbox.root / "out"

    run = run_topdf(app, sandbox, blob, "--out", out)

    assert run.returncode == 1
    assert "spectrum.bin: Failed: spectrum.bin is a binary file, not text" in run.stdout
    assert not list(out.glob("*.pdf"))


def test_the_version_is_the_source_version(app, sandbox):
    version = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    run = run_topdf(app, sandbox, "--version")
    assert run.returncode == 0
    assert run.stdout.strip() == f"topdf {version}"
