"""ci/versions.py: every file holding the version, and the tag, agree."""

import subprocess
import sys
from pathlib import Path

import pytest

from ci import versions

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "ci" / "versions.py"


def version_files(root, pyproject="0.3.0", init="0.3.0", citation="0.3.0", lock="0.3.0"):
    (root / "topdf").mkdir()
    (root / "pyproject.toml").write_text(
        f'[project]\nname = "file-to-pdf"\nversion = "{pyproject}"\n', encoding="utf-8"
    )
    (root / "topdf" / "__init__.py").write_text(f'APP_NAME = "File to PDF"\n__version__ = "{init}"\n', encoding="utf-8")
    (root / "CITATION.cff").write_text(f"cff-version: 1.2.0\nversion: {citation}\n", encoding="utf-8")
    (root / "uv.lock").write_text(
        f'version = 1\n\n[[package]]\nname = "altgraph"\nversion = "0.17.5"\n\n'
        f'[[package]]\nname = "file-to-pdf"\nversion = "{lock}"\nsource = {{ virtual = "." }}\n',
        encoding="utf-8",
    )
    return root


def test_matching_files_agree(tmp_path):
    ok, message = versions.verdict(versions.read_versions(version_files(tmp_path)))
    assert ok is True
    assert message.endswith("all say 0.3.0.")


@pytest.mark.parametrize("file", ["pyproject", "init", "citation", "lock"])
def test_one_file_behind_fails(tmp_path, file):
    ok, message = versions.verdict(versions.read_versions(version_files(tmp_path, **{file: "0.2.0"})))
    assert ok is False
    assert "The versions differ" in message
    assert "0.2.0" in message


def test_a_lock_without_the_project_fails(tmp_path):
    version_files(tmp_path)
    (tmp_path / "uv.lock").write_text('version = 1\n\n[[package]]\nname = "altgraph"\nversion = "0.3.0"\n')
    ok, message = versions.verdict(versions.read_versions(tmp_path))
    assert ok is False
    assert message == "No version found in uv.lock."


@pytest.mark.parametrize("tag, ok", [("v0.3.0", True), ("v0.2.0", False), ("0.3.0", False), ("v0.3.0-test.1", False)])
def test_the_tag_must_match(tmp_path, tag, ok):
    assert versions.verdict(versions.read_versions(version_files(tmp_path)), tag)[0] is ok


def test_this_repository_agrees():
    ok, message = versions.verdict(versions.read_versions(ROOT))
    assert ok, message


def run(cwd, *args):
    return subprocess.run([sys.executable, str(SCRIPT), *args], cwd=cwd, capture_output=True, text=True)


def test_the_script(tmp_path):
    version_files(tmp_path)
    assert run(tmp_path).returncode == 0
    assert run(tmp_path, "--tag", "v0.3.0").returncode == 0
    wrong = run(tmp_path, "--tag", "v0.2.0")
    assert wrong.returncode == 1
    assert "doesn't match" in wrong.stderr


def test_the_script_without_the_files_fails(tmp_path):
    result = run(tmp_path)
    assert result.returncode == 1
    assert "Can't read the version files" in result.stderr


@pytest.mark.parametrize("args", [["--tag"], ["v0.3.0"], ["--tag", "v0.3.0", "extra"]])
def test_the_script_rejects_other_arguments(tmp_path, args):
    version_files(tmp_path)
    assert run(tmp_path, *args).returncode == 1
