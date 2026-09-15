"""ci/checksums.py: SHA256SUMS in sha256sum's format, checked against the files and the build's hash."""

import hashlib
import subprocess
import sys
from pathlib import Path

from ci import checksums

SCRIPT = Path(__file__).resolve().parents[1] / "ci" / "checksums.py"


def files(tmp_path):
    (tmp_path / "FileToPDF-windows.zip").write_bytes(b"zip bytes")
    (tmp_path / "FileToPDF-windows.cdx.json").write_text('{"bomFormat": "CycloneDX"}\n', encoding="utf-8")
    return [tmp_path / "FileToPDF-windows.zip", tmp_path / "FileToPDF-windows.cdx.json"]


def test_write_uses_sha256sums_format_with_lf(tmp_path):
    zip_file, sbom = files(tmp_path)
    checksums.write(tmp_path / "SHA256SUMS", [zip_file, sbom])
    data = (tmp_path / "SHA256SUMS").read_bytes()
    assert b"\r" not in data
    assert data.decode() == (
        f"{hashlib.sha256(b'zip bytes').hexdigest()}  FileToPDF-windows.zip\n"
        f"{hashlib.sha256(sbom.read_bytes()).hexdigest()}  FileToPDF-windows.cdx.json\n"
    )


def test_matching_files_pass(tmp_path):
    zip_file, sbom = files(tmp_path)
    checksums.write(tmp_path / "SHA256SUMS", [zip_file, sbom])
    assert checksums.check(tmp_path / "SHA256SUMS", {"FileToPDF-windows.zip": checksums.sha256(zip_file)}) == []


def test_a_changed_file_fails(tmp_path):
    zip_file, sbom = files(tmp_path)
    checksums.write(tmp_path / "SHA256SUMS", [zip_file, sbom])
    zip_file.write_bytes(b"zip bytes, changed after hashing")
    problems = checksums.check(tmp_path / "SHA256SUMS", {})
    assert len(problems) == 1
    assert problems[0].startswith("FileToPDF-windows.zip has SHA-256")


def test_a_file_replaced_with_its_line_fails_against_the_expected_hash(tmp_path):
    zip_file, sbom = files(tmp_path)
    built = checksums.sha256(zip_file)
    zip_file.write_bytes(b"another zip")
    checksums.write(tmp_path / "SHA256SUMS", [zip_file, sbom])
    assert checksums.check(tmp_path / "SHA256SUMS", {}) == []
    problems = checksums.check(tmp_path / "SHA256SUMS", {"FileToPDF-windows.zip": built})
    assert problems and "but it should be" in problems[0]


def test_a_missing_file_and_an_empty_list_fail(tmp_path):
    zip_file, _ = files(tmp_path)
    checksums.write(tmp_path / "SHA256SUMS", [zip_file])
    zip_file.unlink()
    assert "missing" in checksums.check(tmp_path / "SHA256SUMS", {})[0]
    (tmp_path / "EMPTY").write_text("", encoding="utf-8")
    assert "lists no files" in checksums.check(tmp_path / "EMPTY", {})[0]


def test_parse_reads_text_and_binary_lines():
    assert checksums.parse("ABC  a.zip\ndef *b.json\n\n") == {"a.zip": "abc", "b.json": "def"}


def run(*args, cwd):
    return subprocess.run([sys.executable, str(SCRIPT), *args], cwd=cwd, capture_output=True, text=True)


def test_the_script(tmp_path):
    zip_file, sbom = files(tmp_path)
    assert run("write", "SHA256SUMS", zip_file.name, sbom.name, cwd=tmp_path).returncode == 0
    good = run("check", "SHA256SUMS", f"{zip_file.name}={checksums.sha256(zip_file)}", cwd=tmp_path)
    assert good.returncode == 0, good.stderr
    wrong = run("check", "SHA256SUMS", f"{zip_file.name}={'0' * 64}", cwd=tmp_path)
    assert wrong.returncode == 1
    assert "but it should be" in wrong.stderr
    assert run("check", "SHA256SUMS", "not-a-pair", cwd=tmp_path).returncode == 1
    # An expected hash that didn't reach the job (an empty job output) fails too.
    empty = run("check", "SHA256SUMS", f"{zip_file.name}=", cwd=tmp_path)
    assert empty.returncode == 1
    assert "NAME=SHA256" in empty.stderr
    assert run("write", "SHA256SUMS", cwd=tmp_path).returncode == 1
