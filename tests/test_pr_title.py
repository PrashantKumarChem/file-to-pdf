"""ci/pr_title.py: conventional titles, and no branch commit release-please would read as a second change."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

from ci import pr_title

SCRIPT = Path(__file__).resolve().parents[1] / "ci" / "pr_title.py"


@pytest.mark.parametrize(
    "title",
    [
        "fix: keep spaces in file names",
        "feat(gui): drop files on the window",
        "feat!: need Python 3.14",
        "deps: bump the python group across 1 directory with 3 updates",
        "ci: check pull request titles",
        # release-please's own Release PR.
        "chore(main): release 0.3.0",
    ],
)
def test_conventional_titles_pass(title):
    assert pr_title.title_verdict(title)[0] is True


@pytest.mark.parametrize(
    "title",
    [
        "",
        "Add a thing",
        "Fix: a capital letter in the type",
        "fix:no space after the colon",
        "fix : a space before the colon",
        "fix: ",
        "feature: not a type",
        "fix(): an empty scope",
        "fix(a(b)): nested parentheses",
        "Merge pull request #40 from PrashantKumarChem/feat/x",
    ],
)
def test_other_titles_fail(title):
    ok, message = pr_title.title_verdict(title)
    assert ok is False
    assert "isn't a conventional commit" in message


@pytest.mark.parametrize(
    "title, effect",
    [
        ("feat: x", "raises the minor version"),
        ("fix: x", "raises the patch version"),
        ("deps: x", "raises the patch version"),
        ("docs: x", "doesn't make a release"),
        ("chore!: x", "breaking change"),
    ],
)
def test_the_verdict_says_what_the_title_does_to_the_version(title, effect):
    assert effect in pr_title.title_verdict(title)[1]


@pytest.mark.parametrize(
    "message, line",
    [
        ("Check pull request titles\n\nThe body explains why.", None),
        ("Mention fix: in the middle of a sentence", None),
        ("docs: explain the release types", None),
        ("chore(main): release 0.3.0", None),
        ("feat: add a thing", "feat: add a thing"),
        ("fix(gui): keep the window open\n\nWhy it closed.", "fix(gui): keep the window open"),
        # release-please splits a body paragraph that starts with a type into a commit of its own.
        ("A plain summary\n\nfix: a second change in the body", "fix: a second change in the body"),
        ("A plain summary\r\n\r\nperf: a CRLF body", "perf: a CRLF body"),
        ("docs!: drop the old guide", "docs!: drop the old guide"),
        ("chore: tidy up\n\nBREAKING CHANGE: the log moves", "chore: tidy up"),
    ],
)
def test_releasing_paragraph(message, line):
    assert pr_title.releasing_paragraph(message) == line


def git(repo, *args, author="test"):
    return subprocess.run(
        ["git", "-c", f"user.name={author}", "-c", "user.email=test@example.invalid", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        encoding="utf-8",
    ).stdout.strip()


def branch(repo, *commits):
    """A repository with a base commit and a branch of (author, message) commits; returns (base, head)."""
    git(repo, "init", "-q")
    # Already on the base branch, so never checked.
    git(repo, "commit", "-q", "--allow-empty", "-m", "fix: an older change on main")
    base = git(repo, "rev-parse", "HEAD")
    for author, message in commits:
        git(repo, "commit", "-q", "--allow-empty", "-m", message, author=author)
    return base, git(repo, "rev-parse", "HEAD")


def check(repo, title, base, head):
    return subprocess.run(
        [sys.executable, str(SCRIPT), base, head],
        cwd=repo,
        capture_output=True,
        text=True,
        env={**os.environ, "PR_TITLE": title},
    )


def test_a_conventional_title_with_plain_commits_passes(tmp_path):
    base, head = branch(tmp_path, ("test", "Check the titles"), ("test", "Explain the types\n\nIn CONTRIBUTING."))
    run = check(tmp_path, "ci: check pull request titles", base, head)
    assert run.returncode == 0, run.stdout + run.stderr
    assert "None of the branch's 2 commits" in run.stdout


def test_a_conventional_branch_commit_fails(tmp_path):
    base, head = branch(tmp_path, ("test", "Check the titles"), ("test", "feat: add a thing"))
    run = check(tmp_path, "feat: add a thing", base, head)
    assert run.returncode == 1
    assert '"feat: add a thing", which release-please would read as a change of its own' in run.stderr


def test_dependabot_commits_are_exempt(tmp_path):
    base, head = branch(tmp_path, ("dependabot[bot]", "deps: bump pytest from 9.1.1 to 9.1.2"))
    run = check(tmp_path, "deps: bump pytest from 9.1.1 to 9.1.2", base, head)
    assert run.returncode == 0, run.stdout + run.stderr


def test_a_plain_title_fails_even_with_plain_commits(tmp_path):
    base, head = branch(tmp_path, ("test", "Check the titles"))
    run = check(tmp_path, "Check pull request titles", base, head)
    assert run.returncode == 1
    assert "isn't a conventional commit" in run.stderr


def test_an_unknown_base_fails(tmp_path):
    _, head = branch(tmp_path, ("test", "Check the titles"))
    run = check(tmp_path, "ci: check pull request titles", "0" * 40, head)
    assert run.returncode == 1
    assert "Can't list the commits" in run.stderr
