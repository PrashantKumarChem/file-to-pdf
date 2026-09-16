"""ci/ship.py: the monthly ship merges a Release PR only when it is clean, labelled and not empty."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from ci import ship

SCRIPT = Path(__file__).resolve().parents[1] / "ci" / "ship.py"

PULL = {
    "number": 50,
    "title": "chore(main): release 0.3.0",
    "draft": False,
    "mergeable": True,
    "mergeable_state": "clean",
    "labels": [{"name": "autorelease: pending"}],
    "head": {"sha": "9f0dd1e5cfa1", "ref": "release-please--branches--main"},
}
NOTES = "### Features\n\n* build the app with Python 3.14 ([#47](https://example.invalid/47))\n"


def state(pull: dict | None = None, **changes) -> dict:
    """A shippable state, with the changes applied; pull replaces fields of the pull request."""
    ready = {"numbers": [50], "pull": PULL | (pull or {}), "manifest": {".": "0.3.0"}, "notes": NOTES}
    return ready | changes


def test_a_clean_release_pr_ships():
    decision, reason, outputs = ship.verdict(state())
    assert decision == "ship"
    assert "releases 0.3.0" in reason
    assert outputs == {"number": "50", "head": "9f0dd1e5cfa1", "version": "0.3.0"}


def test_no_release_pr_is_no_failure():
    decision, reason, outputs = ship.verdict(state(numbers=[], pull=None))
    assert decision == "skip"
    assert "No Release PR is open" in reason
    assert outputs == {}


def test_two_release_prs_need_a_person():
    decision, reason, _ = ship.verdict(state(numbers=[50, 51]))
    assert decision == "error"
    assert "#50, #51" in reason


def test_a_draft_release_pr_waits():
    decision, reason, _ = ship.verdict(state(pull={"draft": True}))
    assert decision == "skip"
    assert "is a draft" in reason


@pytest.mark.parametrize(
    "labels",
    [
        [],
        [{"name": "autorelease: tagged"}],
        [{"name": "release"}, {"name": "autorelease: snapshot"}],
    ],
)
def test_only_a_pending_release_pr_ships(labels):
    decision, reason, _ = ship.verdict(state(pull={"labels": labels}))
    assert decision == "skip"
    assert "autorelease: pending" in reason


@pytest.mark.parametrize("merge_state", ["blocked", "dirty", "unstable", "unknown", "behind", "draft"])
def test_only_a_clean_release_pr_ships(merge_state):
    decision, reason, outputs = ship.verdict(state(pull={"mergeable_state": merge_state}))
    assert decision == "skip"
    assert f'"{merge_state}"' in reason
    assert outputs["number"] == "50"


def test_a_pull_request_github_cant_merge_doesnt_ship():
    decision, _, _ = ship.verdict(state(pull={"mergeable": False}))
    assert decision == "skip"
    decision, _, _ = ship.verdict(state(pull={"mergeable": None}))
    assert decision == "skip"


@pytest.mark.parametrize("notes", ["", "   \n\n", None])
def test_a_release_without_notes_doesnt_ship(notes):
    decision, reason, _ = ship.verdict(state(notes=notes))
    assert decision == "skip"
    assert "with no notes" in reason


def test_a_manifest_without_a_version_needs_a_person():
    decision, reason, _ = ship.verdict(state(manifest={}))
    assert decision == "error"
    assert "No version" in reason


def test_a_title_that_doesnt_name_the_version_needs_a_person():
    decision, reason, _ = ship.verdict(state(pull={"title": "chore(main): release 0.4.0"}))
    assert decision == "error"
    assert "doesn't name 0.3.0" in reason


def test_a_state_missing_the_pull_request_needs_a_person():
    decision, reason, _ = ship.verdict(state(pull=None, numbers=[50]) | {"pull": None})
    assert decision == "error"
    assert "#50 itself" in reason
    decision, _, _ = ship.verdict(state() | {"pull": {"number": 51}})
    assert decision == "error"


def run(tmp_path: Path, value) -> subprocess.CompletedProcess:
    written = tmp_path / "state.json"
    written.write_text(json.dumps(value), encoding="utf-8")
    return subprocess.run(
        [sys.executable, str(SCRIPT), str(written)],
        capture_output=True,
        encoding="utf-8",
        cwd=SCRIPT.parents[1],
    )


def test_the_script_prints_the_outputs_of_a_ship(tmp_path):
    done = run(tmp_path, state())
    assert done.returncode == 0
    assert done.stdout.splitlines() == ["ship=true", "number=50", "head=9f0dd1e5cfa1", "version=0.3.0"]
    assert "releases 0.3.0" in done.stderr


def test_the_script_prints_ship_false_for_a_quiet_month(tmp_path):
    done = run(tmp_path, state(numbers=[], pull=None))
    assert done.returncode == 0
    assert done.stdout.splitlines() == ["ship=false"]


def test_the_script_fails_on_a_state_that_needs_a_person(tmp_path):
    done = run(tmp_path, state(numbers=[50, 51]))
    assert done.returncode == 1
    assert done.stdout.splitlines()[0] == "ship=false"


def test_the_script_fails_on_a_state_it_cant_read(tmp_path):
    missing = subprocess.run(
        [sys.executable, str(SCRIPT), str(tmp_path / "nothing.json")],
        capture_output=True,
        encoding="utf-8",
    )
    assert missing.returncode == 1
    assert "Can't read the state" in missing.stderr
    assert run(tmp_path, [1, 2]).returncode == 1


def test_the_script_wants_one_argument():
    done = subprocess.run([sys.executable, str(SCRIPT)], capture_output=True, encoding="utf-8")
    assert done.returncode == 1
    assert "python3 ci/ship.py STATE" in done.stderr
