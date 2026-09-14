"""ci/coverage_floor.py: the coverage floor may stay or rise, never fall."""

import subprocess
import sys
from pathlib import Path

import pytest

from ci import coverage_floor

SCRIPT = Path(__file__).resolve().parents[1] / "ci" / "coverage_floor.py"


def pyproject(floor=None):
    return "[tool.coverage.report]\nprecision = 1\n" + ("" if floor is None else f"fail_under = {floor}\n")


@pytest.mark.parametrize(
    "current, base, ok",
    [
        (91.2, 91.2, True),
        (92, 91.2, True),
        (91.1, 91.2, False),
        (None, 91.2, False),
        (90, None, True),
        (None, None, True),
    ],
)
def test_verdict(current, base, ok):
    assert coverage_floor.verdict(current, base)[0] is ok


def test_floor_is_read_from_the_report_table():
    assert coverage_floor.floor_in(pyproject(91.2)) == 91.2
    assert coverage_floor.floor_in(pyproject(90)) == 90.0
    assert coverage_floor.floor_in(pyproject()) is None
    assert coverage_floor.floor_in("[project]\nname = 'x'\n") is None


def git(repo, *args):
    subprocess.run(
        ["git", "-c", "user.name=test", "-c", "user.email=test@example.invalid", *args],
        cwd=repo,
        check=True,
        capture_output=True,
    )


@pytest.mark.parametrize("new_floor, exit_code", [(91.5, 0), (91.2, 0), (90, 1)])
def test_against_the_parent_commit(tmp_path, new_floor, exit_code):
    git(tmp_path, "init", "-q")
    (tmp_path / "pyproject.toml").write_text(pyproject(91.2), encoding="utf-8")
    git(tmp_path, "add", "pyproject.toml")
    git(tmp_path, "commit", "-q", "-m", "base")
    (tmp_path / "pyproject.toml").write_text(pyproject(new_floor), encoding="utf-8")
    git(tmp_path, "commit", "-q", "--allow-empty", "-am", "change")
    run = subprocess.run([sys.executable, str(SCRIPT)], cwd=tmp_path, capture_output=True, text=True)
    assert run.returncode == exit_code, run.stdout + run.stderr


def test_a_missing_base_revision_fails(tmp_path):
    git(tmp_path, "init", "-q")
    (tmp_path / "pyproject.toml").write_text(pyproject(91.2), encoding="utf-8")
    git(tmp_path, "add", "pyproject.toml")
    git(tmp_path, "commit", "-q", "-m", "only commit")
    run = subprocess.run([sys.executable, str(SCRIPT)], cwd=tmp_path, capture_output=True, text=True)
    assert run.returncode == 1
    assert "Can't read pyproject.toml at HEAD^1" in run.stderr
