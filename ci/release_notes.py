"""Write a release's notes from CHANGELOG.md, and say whether it is a prerelease.

    python3 ci/release_notes.py TAG CHANGELOG NOTES

TAG is the release's tag, such as v0.3.0. NOTES gets that version's section of
CHANGELOG, without its heading: release-please writes "## [0.3.0](compare
link) (date)", and the older sections are "## [0.2.0] - date". A section ends
at the next "## " heading or at the link definitions that close the file.

Prints "prerelease=true" or "prerelease=false" for $GITHUB_OUTPUT. A version
of numbers and dots only (0.3.0) is a final release; any other (0.0.0.dev1,
1.0.0rc1) is a prerelease, which is never marked Latest.

Needs only Python's standard library. Exit code 0 when NOTES was written, 1
when the tag isn't v<version> or the version's section is missing or empty.
"""

import re
import sys
from pathlib import Path

LINK_DEFINITION = re.compile(r"^\[[^\]]+\]:\s")


def is_prerelease(version: str) -> bool:
    return re.fullmatch(r"\d+(\.\d+)*", version) is None


def section(changelog: str, version: str) -> str | None:
    """The text of version's section, without its heading; None when no heading names version."""
    heading = re.compile(rf"^## \[?{re.escape(version)}\]?(?=[\s(]|$)")
    lines = changelog.splitlines()
    start = next((number for number, line in enumerate(lines) if heading.match(line)), None)
    if start is None:
        return None
    body = []
    for line in lines[start + 1 :]:
        if line.startswith("## ") or LINK_DEFINITION.match(line):
            break
        body.append(line)
    return "\n".join(body).strip()


def main(argv: list[str]) -> int:
    if len(argv) != 4 or not re.fullmatch(r"v\S+", argv[1]):
        print(__doc__, file=sys.stderr)
        return 1
    version, changelog, notes = argv[1][1:], Path(argv[2]), Path(argv[3])
    try:
        text = section(changelog.read_text(encoding="utf-8"), version)
        if not text:
            print(f'{changelog} has no notes for {version}: add a "## [{version}]" section.', file=sys.stderr)
            return 1
        notes.write_text(text + "\n", encoding="utf-8", newline="\n")
    except OSError as error:
        print(f"Can't write the release notes: {error}", file=sys.stderr)
        return 1
    print(f"Notes for {version}: {len(text.splitlines())} lines, written to {notes}.", file=sys.stderr)
    print(f"prerelease={'true' if is_prerelease(version) else 'false'}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
