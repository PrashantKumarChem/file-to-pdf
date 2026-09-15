"""Check a pull request's title and its branch's commits for release-please.

    PR_TITLE="fix: ..." python ci/pr_title.py BASE HEAD

release-please decides the next version and writes the changelog from
conventional commit messages on main. Pull requests are merged with a merge
commit whose body is the pull request's title, so the title is the message it
reads there: it must be a conventional commit, such as "fix: keep spaces in
file names". The branch's own commits (BASE..HEAD, merge commits left out) land
on main too, and release-please would read a conventional one among them as a
second change and list it twice. So no commit may start a paragraph with a type
that makes a release. Dependabot's commits are exempt: Dependabot gives each
commit the pull request's title.

Exit code 0 when the title and the commits pass, 1 when either doesn't or when
git can't list the commits.
"""

import os
import re
import subprocess
import sys

TYPES = ("feat", "fix", "perf", "deps", "revert", "docs", "style", "chore", "refactor", "test", "build", "ci")
# The types that make a release on their own. The others do only when marked
# as a breaking change.
RELEASING = ("feat", "fix", "perf", "deps", "revert")
DEPENDABOT = "dependabot[bot]"

TITLE = re.compile(r"(?P<type>[a-z]+)(?:\((?P<scope>[^()\s]+)\))?(?P<breaking>!)?: (?P<subject>\S.*)")
# Looser than TITLE on purpose: any line release-please might parse as a commit.
COMMIT_HEADER = re.compile(r"(?P<type>[a-z]+)(?:\([^()\r\n]*\))?(?P<breaking>!)?:")
BREAKING_FOOTER = re.compile(r"^BREAKING[ -]CHANGE:", re.MULTILINE)
PARAGRAPH_BREAK = re.compile(r"\r?\n[ \t]*\r?\n")


def title_verdict(title: str) -> tuple[bool, str]:
    match = TITLE.fullmatch(title)
    if match is None or match["type"] not in TYPES:
        return False, (
            f"The title {title!r} isn't a conventional commit. Start it with a type, a colon and a space, "
            f'for example "fix: keep spaces in file names". The types are {", ".join(TYPES)}.'
        )
    kind = match["type"]
    if match["breaking"]:
        effect = "marks a breaking change, which raises the minor version while the version is below 1.0.0"
    elif kind == "feat":
        effect = "raises the minor version"
    elif kind in RELEASING:
        effect = "raises the patch version"
    else:
        effect = "doesn't make a release on its own"
    return True, f'The title is a "{kind}" commit, which {effect}.'


def releasing_paragraph(message: str) -> str | None:
    """The first line release-please would read as a change that makes a release, if any."""
    breaking_footer = BREAKING_FOOTER.search(message) is not None
    for paragraph in PARAGRAPH_BREAK.split(message.strip()):
        lines = paragraph.strip().splitlines()
        match = COMMIT_HEADER.match(lines[0]) if lines else None
        if match and match["type"] in TYPES and (match["type"] in RELEASING or match["breaking"] or breaking_footer):
            return lines[0]
    return None


def commit_problems(commits: list[tuple[str, str, str]]) -> list[str]:
    problems = []
    for sha, author, message in commits:
        if author == DEPENDABOT:
            continue
        line = releasing_paragraph(message)
        if line is not None:
            problems.append(
                f'Commit {sha[:7]} has the line "{line}", which release-please would read as a change of its own, '
                "apart from the pull request's title, and list twice. Reword that commit as a plain sentence "
                "(git rebase -i, then git push --force-with-lease); the title says what the pull request releases."
            )
    return problems


def branch_commits(base: str, head: str) -> list[tuple[str, str, str]]:
    """(sha, author name, message) of each commit in BASE..HEAD that isn't a merge commit."""
    listed = subprocess.run(
        ["git", "log", "--no-merges", "--format=%H%x1f%an%x1f%B%x1e", f"{base}..{head}"],
        capture_output=True,
        encoding="utf-8",
        check=True,
    )
    commits = []
    for record in listed.stdout.split("\x1e"):
        if not record.strip():
            continue
        sha, author, message = record.lstrip("\n").split("\x1f", 2)
        commits.append((sha, author, message))
    return commits


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(__doc__, file=sys.stderr)
        return 1
    error = "::error::" if os.environ.get("GITHUB_ACTIONS") == "true" else ""
    ok, message = title_verdict(os.environ.get("PR_TITLE", ""))
    print(message if ok else error + message, file=sys.stdout if ok else sys.stderr)
    try:
        commits = branch_commits(argv[1], argv[2])
    except subprocess.CalledProcessError as failure:
        print(f"{error}Can't list the commits in {argv[1]}..{argv[2]}: {failure.stderr.strip()}", file=sys.stderr)
        return 1
    problems = commit_problems(commits)
    for problem in problems:
        print(error + problem, file=sys.stderr)
    if not problems:
        print(f"None of the branch's {len(commits)} commits would be read as a change of its own.")
    return 0 if ok and not problems else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
