"""Write or check SHA-256 checksums, in sha256sum's format.

    python ci/checksums.py write SHA256SUMS FILE...
    python ci/checksums.py check SHA256SUMS [NAME=SHA256]...

"write" writes "<sha256>  <file name>" for each file, with LF line endings, so
"sha256sum -c SHA256SUMS" also checks it. "check" hashes every file SHA256SUMS
lists, from the folder SHA256SUMS is in, and compares. Each NAME=SHA256 must
match SHA256SUMS too, so a file replaced together with its line in SHA256SUMS
doesn't pass: the build job hands the zip's hash to later jobs this way.

Exit code 0 when everything matches, 1 otherwise.
"""

import hashlib
import sys
from pathlib import Path


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for block in iter(lambda: file.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def parse(text: str) -> dict[str, str]:
    """File name to hash, from sha256sum's text or binary line format."""
    sums = {}
    for line in text.splitlines():
        if not line.strip():
            continue
        digest, _, name = line.partition(" ")
        sums[name.removeprefix(" ").removeprefix("*")] = digest.lower()
    return sums


def write(sums_path: Path, files: list[Path]) -> None:
    lines = "".join(f"{sha256(file)}  {file.name}\n" for file in files)
    sums_path.write_text(lines, encoding="utf-8", newline="\n")


def check(sums_path: Path, expected: dict[str, str]) -> list[str]:
    """What doesn't match; empty when everything does."""
    sums = parse(sums_path.read_text(encoding="utf-8"))
    problems = []
    if not sums:
        problems.append(f"{sums_path.name} lists no files.")
    for name, digest in sums.items():
        file = sums_path.parent / name
        if not file.is_file():
            problems.append(f"{name} is listed in {sums_path.name} but missing.")
        elif sha256(file) != digest:
            problems.append(f"{name} has SHA-256 {sha256(file)}, but {sums_path.name} says {digest}.")
    for name, digest in expected.items():
        if sums.get(name) != digest.lower():
            problems.append(f"{sums_path.name} says {name} is {sums.get(name)}, but it should be {digest.lower()}.")
    return problems


def main(argv: list[str]) -> int:
    if len(argv) < 3 or argv[1] not in ("write", "check") or (argv[1] == "write" and len(argv) < 4):
        print(__doc__, file=sys.stderr)
        return 1
    sums_path = Path(argv[2])
    try:
        if argv[1] == "write":
            write(sums_path, [Path(name) for name in argv[3:]])
            print(sums_path.read_text(encoding="utf-8"), end="")
            return 0
        pairs = [pair.partition("=") for pair in argv[3:]]
        if any(not sep or not name or not digest for name, sep, digest in pairs):
            print("Expected hashes are given as NAME=SHA256.", file=sys.stderr)
            return 1
        problems = check(sums_path, {name: digest for name, _, digest in pairs})
    except OSError as error:
        print(f"Can't read the files: {error}", file=sys.stderr)
        return 1
    for problem in problems:
        print(problem, file=sys.stderr)
    if not problems:
        also = ", and so do the expected hashes." if pairs else "."
        print(f"Every file in {sums_path.name} matches its SHA-256{also}")
    return 0 if not problems else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
