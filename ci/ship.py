"""Decide whether the monthly ship should merge the Release PR.

    python3 ci/ship.py STATE

STATE is the JSON the Ship workflow (.github/workflows/ship.yml) gathers:

    numbers    the numbers of the open pull requests on release-please's branch
    pull       that pull request, as the API returns it, when there is one
    manifest   .release-please-manifest.json at its head commit
    notes      the notes ci/release_notes.py writes from CHANGELOG.md at its
               head commit, which are the notes the release would carry, or
               an empty string when it writes none

It ships only a Release PR that is open and no draft, still labelled
"autorelease: pending", that GitHub calls "clean" (mergeable, with every check
the main ruleset requires passed), whose version every file agrees on, and
whose notes aren't empty. Prints "ship=true" or "ship=false" with the pull
request's number, version and head commit for $GITHUB_OUTPUT, and the reason
on stderr.

Deciding not to ship is not a failure: most months have nothing to release,
and a Release PR whose checks are still running is shipped by the next run, or
by hand at any time. Exit code 0 when a decision is made, 1 when STATE can't
be read or says something that needs a person, such as two Release PRs.
"""

import json
import sys
from pathlib import Path

# release-please labels the Release PR this until the Release workflow tags
# its merge commit, when the label becomes "autorelease: tagged".
PENDING = "autorelease: pending"
# GitHub's own word for a pull request it would merge: no conflict with main,
# and every check the ruleset requires passed.
CLEAN = "clean"


def verdict(state: dict) -> tuple[str, str, dict[str, str]]:
    """Whether to "ship" or to "skip", or to stop with an "error"; why; and the outputs."""
    numbers = state.get("numbers") or []
    if len(numbers) > 1:
        listed = ", ".join(f"#{number}" for number in numbers)
        return "error", f"{len(numbers)} Release PRs are open ({listed}); release-please keeps one.", {}
    if not numbers:
        return "skip", "No Release PR is open, so nothing that makes a release has been merged.", {}
    number = numbers[0]
    pull = state.get("pull")
    if not isinstance(pull, dict) or pull.get("number") != number:
        return "error", f"The state doesn't hold Release PR #{number} itself.", {}
    outputs = {"number": str(number), "head": (pull.get("head") or {}).get("sha", "")}
    if pull.get("draft"):
        return "skip", f"Release PR #{number} is a draft.", outputs
    labels = [label.get("name") for label in pull.get("labels") or []]
    if PENDING not in labels:
        listed = ", ".join(f'"{label}"' for label in labels) or "no labels"
        return "skip", f'Release PR #{number} has {listed}, not "{PENDING}".', outputs
    merge_state = pull.get("mergeable_state")
    if pull.get("mergeable") is not True or merge_state != CLEAN:
        return (
            "skip",
            (
                f'GitHub calls Release PR #{number} "{merge_state}", not "{CLEAN}", so it would not merge it as '
                'it is: "blocked" means a check the ruleset requires hasn\'t passed, "dirty" a conflict with '
                'main, "unstable" a check that failed without being required, "unknown" that GitHub is still '
                "working it out."
            ),
            outputs,
        )
    version = (state.get("manifest") or {}).get(".")
    if not version:
        return "error", f"No version in .release-please-manifest.json at Release PR #{number}'s head.", outputs
    outputs["version"] = version
    title = pull.get("title") or ""
    if version not in title:
        return "error", f'Release PR #{number} is titled "{title}", which doesn\'t name {version}.', outputs
    notes = (state.get("notes") or "").strip()
    if not notes:
        return (
            "skip",
            (
                f"Release PR #{number} would release {version} with no notes: CHANGELOG.md at its head has no "
                f"section for {version}, or an empty one."
            ),
            outputs,
        )
    return (
        "ship",
        (
            f"Release PR #{number} releases {version}, GitHub calls it {CLEAN}, and its notes are "
            f"{len(notes.splitlines())} lines."
        ),
        outputs,
    )


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__, file=sys.stderr)
        return 1
    try:
        state = json.loads(Path(argv[1]).read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        print(f"Can't read the state in {argv[1]}: {error}", file=sys.stderr)
        return 1
    if not isinstance(state, dict):
        print(f"{argv[1]} holds {type(state).__name__}, not an object.", file=sys.stderr)
        return 1
    decision, reason, outputs = verdict(state)
    print(reason, file=sys.stderr)
    print(f"ship={'true' if decision == 'ship' else 'false'}")
    for name, value in outputs.items():
        print(f"{name}={value}")
    return 1 if decision == "error" else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
