"""ci/accept_rendering.py against a fake GitHub: what it refuses, what it commits, what it approves."""

import json

import pytest

from ci import accept_rendering as accept

REPO = "owner/file-to-pdf"
HEAD = "1" * 40
TREE = "t" * 40
GOOD = {
    "format": 1,
    "dpi": 96,
    "files": {
        "a.json": {"title": "", "pages": [{"pixels": "a" * 64, "lines": "b" * 64, "line_count": 1, "links": []}]}
    },
}


class FakeGitHub(accept.GitHub):
    def __init__(self, pr=None, runs=None, artifact=GOOD, same_tree=False, waiting=(11, 12)):
        super().__init__(REPO)
        self.pr = pr or {"state": "open", "head": {"sha": HEAD, "ref": "deps/update", "repo": {"full_name": REPO}}}
        self.runs = [
            {"id": 7, "run_number": 3, "status": "completed", "event": "pull_request", "conclusion": "failure"}
        ]
        if runs is not None:
            self.runs = runs
        self.artifact = artifact
        self.same_tree = same_tree
        self.waiting = list(waiting)
        self.calls = []

    def api(self, path, method="GET", payload=None):
        self.calls.append((method, path, payload))
        if path == "pulls/5":
            return self.pr
        if path.startswith("actions/workflows/visual.yml/runs"):
            return {"workflow_runs": self.runs}
        if path == f"git/commits/{HEAD}":
            return {"tree": {"sha": TREE}}
        if path == "git/blobs":
            return {"sha": "blob"}
        if path == "git/trees":
            return {"sha": TREE if self.same_tree else "newtree"}
        if path == "git/commits":
            return {"sha": "2" * 40}
        if path.startswith("git/refs/heads/"):
            return {}
        if path.startswith("actions/runs?head_sha="):
            return {"workflow_runs": [{"id": i, "conclusion": "action_required"} for i in self.waiting]}
        if path.endswith("/approve"):
            return None
        raise AssertionError(f"unexpected call {method} {path}")

    def download_artifact(self, run_id, name, dest):
        self.calls.append(("DOWNLOAD", str(run_id), name))
        if self.artifact is not None:
            (dest / "fingerprints.json").write_text(json.dumps(self.artifact), encoding="utf-8")


@pytest.fixture(autouse=True)
def no_waiting(monkeypatch):
    real = accept.approve_waiting_runs

    def fast(github, sha, **kwargs):
        ticks = iter(range(1000))
        return real(github, sha, wait=100, quiet=2, poll=1, sleep=lambda s: None, clock=lambda: next(ticks))

    monkeypatch.setattr(accept, "approve_waiting_runs", fast)


def test_commits_the_measured_fingerprints_on_the_head_and_approves_the_new_runs(tmp_path):
    github = FakeGitHub()
    text = accept.accept(github, 5, tmp_path, "maintainer")
    by_path = {(m, p): payload for m, p, payload in github.calls}
    tree = by_path[("POST", "git/trees")]
    assert tree["base_tree"] == TREE
    assert tree["tree"] == [{"path": "tests/visual/fingerprints.json", "mode": "100644", "type": "blob", "sha": "blob"}]
    commit = by_path[("POST", "git/commits")]
    assert commit["parents"] == [HEAD]
    assert "visual comparison run 7" in commit["message"]
    assert by_path[("PATCH", "git/refs/heads/deps/update")] == {"sha": "2" * 40, "force": False}
    assert [p for m, p, _ in github.calls if p.endswith("/approve")] == [
        "actions/runs/11/approve",
        "actions/runs/12/approve",
    ]
    assert "Approved the check runs of that commit: 11, 12." in text


def test_the_committed_bytes_are_the_artifact_bytes(tmp_path):
    github = FakeGitHub()
    accept.accept(github, 5, tmp_path, "maintainer")
    blob = next(payload for m, p, payload in github.calls if p == "git/blobs")
    import base64

    assert json.loads(base64.b64decode(blob["content"])) == GOOD


def test_nothing_is_committed_when_the_branch_already_has_the_fingerprints(tmp_path):
    github = FakeGitHub(same_tree=True)
    assert "nothing committed" in accept.accept(github, 5, tmp_path, "maintainer")
    assert not [c for c in github.calls if c[1] in ("git/commits",) or c[1].startswith("git/refs")]


@pytest.mark.parametrize(
    "kwargs, reason",
    [
        (
            {"pr": {"state": "closed", "head": {"sha": HEAD, "ref": "x", "repo": {"full_name": REPO}}}},
            "closed, not open",
        ),
        (
            {"pr": {"state": "open", "head": {"sha": HEAD, "ref": "x", "repo": {"full_name": "fork/file-to-pdf"}}}},
            "fork",
        ),
        ({"pr": {"state": "open", "head": {"sha": HEAD, "ref": "x", "repo": None}}}, "fork"),
        ({"runs": []}, "No finished visual comparison"),
        (
            {
                "runs": [
                    {"id": 8, "run_number": 4, "status": "completed", "event": "pull_request", "conclusion": "success"}
                ]
            },
            "nothing to accept",
        ),
        (
            {
                "runs": [
                    {
                        "id": 8,
                        "run_number": 4,
                        "status": "completed",
                        "event": "pull_request",
                        "conclusion": "cancelled",
                    }
                ]
            },
            "ended cancelled",
        ),
        ({"artifact": None}, "holds no fingerprints.json"),
        ({"artifact": {"format": 1}}, "malformed"),
    ],
)
def test_refusals_commit_nothing(tmp_path, kwargs, reason):
    github = FakeGitHub(**kwargs)
    with pytest.raises(accept.Refused, match=reason):
        accept.accept(github, 5, tmp_path, "maintainer")
    assert not [c for c in github.calls if c[0] in ("POST", "PATCH")]


def test_the_latest_finished_pull_request_run_is_used(tmp_path):
    runs = [
        {"id": 1, "run_number": 1, "status": "completed", "event": "pull_request", "conclusion": "success"},
        {"id": 2, "run_number": 2, "status": "completed", "event": "pull_request", "conclusion": "failure"},
        {"id": 3, "run_number": 3, "status": "in_progress", "event": "pull_request", "conclusion": None},
        {"id": 4, "run_number": 9, "status": "completed", "event": "push", "conclusion": "success"},
    ]
    github = FakeGitHub(runs=runs)
    accept.accept(github, 5, tmp_path, "maintainer")
    assert ("DOWNLOAD", "2", "visual-comparison") in github.calls


def test_gh_arguments(monkeypatch):
    seen = []

    def run(*args, payload=None):
        seen.append((args, payload))
        return '{"ok": true}'

    github = accept.GitHub(REPO, run=run)
    assert github.api("git/blobs", "POST", {"content": "x"}) == {"ok": True}
    assert seen == [(("api", "--method", "POST", f"repos/{REPO}/git/blobs", "--input", "-"), {"content": "x"})]
