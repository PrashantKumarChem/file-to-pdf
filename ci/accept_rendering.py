"""Accept a pull request's new rendering: commit the fingerprints its visual comparison measured.

    python ci/accept_rendering.py PULL_REQUEST_NUMBER

Run by .github/workflows/accept-rendering.yml, with GH_TOKEN and
GITHUB_REPOSITORY set; needs the gh command and only the standard library.

1. The pull request must be open, with its branch in this repository (the
   workflow can't write to a fork's branch).
2. The latest completed "Visual comparison" run for the pull request's
   current head must have failed, and its visual-comparison artifact must
   hold fingerprints.json. Nothing is rendered again: what gets accepted is
   what the run measured and showed.
3. The fingerprints are validated (ci/visual.py validate), then committed as
   tests/visual/fingerprints.json on top of that head. Moving the branch is
   refused if anyone pushed to it since.
4. A commit made with the workflow's token starts the pull request's checks
   waiting for approval; they are approved, so they run on the new commit.
"""

import base64
import json
import os
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ci import visual

FINGERPRINTS = "tests/visual/fingerprints.json"
VISUAL_WORKFLOW = "visual.yml"
ARTIFACT = "visual-comparison"


class Refused(Exception):
    """The rendering can't be accepted; the message says why."""


def gh(*args: str, payload: dict | None = None) -> str:
    run = subprocess.run(
        ["gh", *args],
        input=None if payload is None else json.dumps(payload),
        capture_output=True,
        encoding="utf-8",
    )
    if run.returncode != 0:
        raise RuntimeError(f"gh {' '.join(args)} failed: {run.stderr.strip()}")
    return run.stdout


class GitHub:
    """The few GitHub calls accepting needs, through gh."""

    def __init__(self, repo: str, run: Callable[..., str] = gh):
        self.repo = repo
        self._run = run

    def api(self, path: str, method: str = "GET", payload: dict | None = None) -> Any:
        args = ["api", "--method", method, f"repos/{self.repo}/{path}"]
        if payload is not None:
            args += ["--input", "-"]
        return json.loads(self._run(*args, payload=payload) or "null")

    def download_artifact(self, run_id: int, name: str, dest: Path) -> None:
        self._run("run", "download", str(run_id), "--repo", self.repo, "--name", name, "--dir", str(dest))


def latest_visual_run(github: GitHub, head_sha: str) -> dict | None:
    runs = github.api(f"actions/workflows/{VISUAL_WORKFLOW}/runs?head_sha={head_sha}&per_page=50")["workflow_runs"]
    completed = [r for r in runs if r["status"] == "completed" and r["event"] == "pull_request"]
    return max(completed, key=lambda r: r["run_number"]) if completed else None


def commit_fingerprints(github: GitHub, branch: str, head_sha: str, content: bytes, message: str) -> str | None:
    """Commit content as FINGERPRINTS on top of head_sha; None if the branch already has it."""
    head = github.api(f"git/commits/{head_sha}")
    blob = github.api("git/blobs", "POST", {"content": base64.b64encode(content).decode("ascii"), "encoding": "base64"})
    tree = github.api(
        "git/trees",
        "POST",
        {
            "base_tree": head["tree"]["sha"],
            "tree": [{"path": FINGERPRINTS, "mode": "100644", "type": "blob", "sha": blob["sha"]}],
        },
    )
    if tree["sha"] == head["tree"]["sha"]:
        return None
    commit = github.api("git/commits", "POST", {"message": message, "tree": tree["sha"], "parents": [head_sha]})
    # force false allows only a fast-forward, which this isn't if anyone pushed after head_sha.
    github.api(f"git/refs/heads/{branch}", "PATCH", {"sha": commit["sha"], "force": False})
    return commit["sha"]


def approve_waiting_runs(
    github: GitHub,
    sha: str,
    wait: float = 90,
    quiet: float = 20,
    poll: float = 5,
    sleep=time.sleep,
    clock=time.monotonic,
) -> list[int]:
    """Approve the runs a commit made with the workflow token started; they wait for approval."""
    approved: list[int] = []
    start = last_new = clock()
    while clock() - start < wait:
        runs = github.api(f"actions/runs?head_sha={sha}&per_page=100")["workflow_runs"]
        for run in runs:
            if run["conclusion"] == "action_required" and run["id"] not in approved:
                github.api(f"actions/runs/{run['id']}/approve", "POST")
                approved.append(run["id"])
                last_new = clock()
        if approved and clock() - last_new >= quiet:
            break
        sleep(poll)
    return approved


def accept(github: GitHub, number: int, workdir: Path, actor: str) -> str:
    pr = github.api(f"pulls/{number}")
    if pr["state"] != "open":
        raise Refused(f"Pull request #{number} is {pr['state']}, not open.")
    if pr["head"]["repo"] is None or pr["head"]["repo"]["full_name"] != github.repo:
        raise Refused(f"Pull request #{number}'s branch is in a fork, which this workflow can't commit to.")
    head_sha, branch = pr["head"]["sha"], pr["head"]["ref"]

    run = latest_visual_run(github, head_sha)
    if run is None:
        raise Refused(f"No finished visual comparison for #{number}'s head {head_sha[:7]} yet; wait for it.")
    if run["conclusion"] == "success":
        raise Refused(f"The visual comparison of {head_sha[:7]} passed (run {run['id']}): nothing to accept.")
    if run["conclusion"] != "failure":
        raise Refused(f"The visual comparison of {head_sha[:7]} ended {run['conclusion']} (run {run['id']}).")

    try:
        github.download_artifact(run["id"], ARTIFACT, workdir)
    except RuntimeError as error:
        raise Refused(f"Run {run['id']} has no usable {ARTIFACT} artifact: {error}") from error
    measured = workdir / "fingerprints.json"
    if not measured.is_file():
        raise Refused(f"Run {run['id']}'s {ARTIFACT} artifact holds no fingerprints.json.")
    content = measured.read_bytes()
    problems = visual.validate(json.loads(content.decode("utf-8")))
    if problems:
        raise Refused("The measured fingerprints are malformed:\n" + "\n".join(problems))

    message = (
        f"Accept the new rendering measured by visual comparison run {run['id']}\n\n"
        f"The fingerprints of the corpus as rendered at {head_sha[:7]}, accepted by {actor}\n"
        f"with the Accept new rendering workflow after looking at run {run['id']}'s pages."
    )
    new_sha = commit_fingerprints(github, branch, head_sha, content, message)
    if new_sha is None:
        return f"#{number}'s branch already holds these fingerprints; nothing committed."
    approved = approve_waiting_runs(github, new_sha)
    runs = ", ".join(str(r) for r in approved) or "none were waiting"
    return (
        f"Committed the fingerprints from run {run['id']} to {branch} as {new_sha[:7]}.\n"
        f"Approved the check runs of that commit: {runs}."
    )


def main(argv: list[str]) -> int:
    number = int(argv[1])
    github = GitHub(os.environ["GITHUB_REPOSITORY"])
    with tempfile.TemporaryDirectory() as workdir:
        try:
            text = accept(github, number, Path(workdir), os.environ.get("GITHUB_ACTOR", "someone"))
        except Refused as refusal:
            print(f"::error::{str(refusal).splitlines()[0]}")
            print(refusal, file=sys.stderr)
            return 1
    print(text)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write(f"## Accept new rendering, pull request #{number}\n\n{text}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
