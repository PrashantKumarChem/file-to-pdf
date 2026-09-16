"""Decide what the "Weekly check failed" issue should say.

    python3 ci/weekly_check.py STATE BODY

STATE is JSON gathered by the workflow (.github/workflows/weekly-check.yml):

    statuses      one entry per watched workflow: {"workflow", "conclusion",
                  "url"}. "conclusion" is the most recent completed run of
                  that workflow whose event is "schedule" or
                  "workflow_dispatch" (the ones that mean "did the outside
                  world break this without any change here"), or null when
                  there isn't one yet.
    issue_open    the number of the open "Weekly check failed" issue, or null

BODY gets one line per workflow, saying whether its latest weekly (or
dispatched) run passed, with a link when there is one to link to.

Prints "action=create", "action=comment", "action=close" or "action=noop" for
$GITHUB_OUTPUT: "create" opens the issue because a workflow is failing and
none is open yet; "comment" adds to the one already open; "close" closes it
because every workflow now passes; "noop" leaves things alone, which is also
what a routine pull request or push run of this script produces, since none
of the four ever fails without one of the workflows itself failing.

Needs only Python's standard library. Exit code 0 always.
"""

import json
import sys
from pathlib import Path


def line(status: dict) -> str:
    workflow, conclusion, url = status.get("workflow", "?"), status.get("conclusion"), status.get("url")
    if conclusion is None:
        return f"- {workflow}: no weekly (or dispatched) run yet."
    if conclusion == "success":
        return f"- {workflow}: passing ([latest run]({url}))." if url else f"- {workflow}: passing."
    return f"- {workflow}: **{conclusion}** ([latest run]({url}))." if url else f"- {workflow}: **{conclusion}**."


def verdict(state: dict) -> tuple[str, str]:
    """The action to take, and the issue body or comment to write."""
    statuses = state.get("statuses") or []
    failing = [status for status in statuses if status.get("conclusion") not in (None, "success")]
    body = "\n".join(
        [
            "The most recent weekly (or dispatched) run of each workflow this watches:",
            "",
            *(line(status) for status in statuses),
        ]
    )
    issue_open = state.get("issue_open")
    if failing:
        return ("comment" if issue_open else "create"), body
    if issue_open:
        return "close", body
    return "noop", body


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(__doc__, file=sys.stderr)
        return 1
    state_path, body_path = Path(argv[1]), Path(argv[2])
    state = json.loads(state_path.read_text(encoding="utf-8"))
    action, body = verdict(state)
    body_path.write_text(body + "\n", encoding="utf-8", newline="\n")
    print(body, file=sys.stderr)
    print(f"action={action}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
