"""ci/release_notes.py: a release's notes from CHANGELOG.md, and whether it is a prerelease."""

import subprocess
import sys
from pathlib import Path

import pytest

from ci import release_notes

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "ci" / "release_notes.py"

CHANGELOG = """# Changelog

Notable changes to File to PDF, newest first.

## [0.3.0](https://github.com/PrashantKumarChem/file-to-pdf/compare/v0.2.0...v0.3.0) (2026-10-01)


### Features

* Convert a folder at once ([#50](https://github.com/PrashantKumarChem/file-to-pdf/issues/50))

## [0.2.0] - 2026-09-14

### Changed

- The app is named File to PDF. (#7)

## [0.1.0] - 2026-09-01

- The first release.

[0.2.0]: https://github.com/PrashantKumarChem/file-to-pdf/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/PrashantKumarChem/file-to-pdf/releases/tag/v0.1.0
"""


def test_a_release_please_section():
    assert release_notes.section(CHANGELOG, "0.3.0") == (
        "### Features\n\n* Convert a folder at once ([#50](https://github.com/PrashantKumarChem/file-to-pdf/issues/50))"
    )


def test_a_section_ends_at_the_next_heading():
    assert release_notes.section(CHANGELOG, "0.2.0") == "### Changed\n\n- The app is named File to PDF. (#7)"


def test_the_last_section_ends_at_the_link_definitions():
    assert release_notes.section(CHANGELOG, "0.1.0") == "- The first release."


@pytest.mark.parametrize("version", ["0.4.0", "0.3", "0.2.0.1", "2.0", "0.1"])
def test_other_versions_have_no_section(version):
    assert release_notes.section(CHANGELOG, version) is None


@pytest.mark.parametrize(
    ("version", "prerelease"),
    [("0.3.0", False), ("10.20.30", False), ("0.0.0.dev1", True), ("1.0.0rc1", True), ("0.0.0+test.1", True)],
)
def test_only_numbers_and_dots_make_a_final_release(version, prerelease):
    assert release_notes.is_prerelease(version) is prerelease


def test_this_changelog_has_the_notes_of_0_2_0():
    notes = release_notes.section((REPO / "CHANGELOG.md").read_text(encoding="utf-8"), "0.2.0")
    assert notes is not None
    assert notes.startswith("### Changed")


def run(*args, cwd):
    return subprocess.run([sys.executable, str(SCRIPT), *args], cwd=cwd, capture_output=True, text=True)


def test_the_script_writes_the_notes_and_prints_the_output(tmp_path):
    test_section = "## [0.0.0.dev1] - test\n\n- A test release.\n"
    (tmp_path / "CHANGELOG.md").write_text(CHANGELOG + test_section, encoding="utf-8")
    final = run("v0.3.0", "CHANGELOG.md", "notes.md", cwd=tmp_path)
    assert final.returncode == 0, final.stderr
    assert final.stdout == "prerelease=false\n"
    data = (tmp_path / "notes.md").read_bytes()
    assert data.startswith(b"### Features\n")
    assert data.endswith(b"issues/50))\n")
    assert b"\r" not in data
    test = run("v0.0.0.dev1", "CHANGELOG.md", "test.md", cwd=tmp_path)
    assert test.returncode == 0, test.stderr
    assert test.stdout == "prerelease=true\n"
    assert (tmp_path / "test.md").read_text(encoding="utf-8") == "- A test release.\n"


def test_the_script_fails_without_notes(tmp_path):
    empty_then_fixed = "## [0.5.0] - 2026-11-01\n\n## [0.4.0] - 2026-10-15\n\n- A fix.\n"
    (tmp_path / "CHANGELOG.md").write_text(empty_then_fixed, encoding="utf-8")
    missing = run("v0.6.0", "CHANGELOG.md", "notes.md", cwd=tmp_path)
    assert missing.returncode == 1
    assert "has no notes for 0.6.0" in missing.stderr
    assert missing.stdout == ""
    assert run("v0.5.0", "CHANGELOG.md", "notes.md", cwd=tmp_path).returncode == 1
    assert not (tmp_path / "notes.md").exists()
    assert run("0.4.0", "CHANGELOG.md", "notes.md", cwd=tmp_path).returncode == 1
    assert run("v0.4.0", "MISSING.md", "notes.md", cwd=tmp_path).returncode == 1
    assert run("v0.4.0", "CHANGELOG.md", "notes.md", cwd=tmp_path).returncode == 0
