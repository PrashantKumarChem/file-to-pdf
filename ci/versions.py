"""Check that every file holding File to PDF's version says the same version.

    python ci/versions.py [--tag TAG]

Run from the repository's root. release-please raises the version in all of
them in its Release PR: .release-please-manifest.json (its record of the
current version), pyproject.toml, topdf/__init__.py, CITATION.cff and the
project's own entry in uv.lock, which "uv lock --check" compares with
pyproject.toml. release-please's updater for uv.lock changes nothing, and
doesn't fail, when its JSONPath stops matching, so this check is what notices.
With --tag, the tag (such as v0.3.0) must match as well.

Exit code 0 when they agree, 1 when they don't or a file can't be read.
"""

import json
import re
import sys
import tomllib
from pathlib import Path

MANIFEST = ".release-please-manifest.json"
INIT = "topdf/__init__.py"
CITATION = "CITATION.cff"


def read_versions(root: Path) -> dict[str, str | None]:
    """The version each file states, or None where it states none."""
    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8")).get("project", {})
    locked = tomllib.loads((root / "uv.lock").read_text(encoding="utf-8")).get("package", [])
    lock = next((package for package in locked if package.get("name") == project.get("name")), {})
    init = re.search(r'^__version__\s*=\s*"([^"]+)"', (root / INIT).read_text(encoding="utf-8"), re.MULTILINE)
    cited = re.search(r"^version:\s*['\"]?([^\s'\"#]+)", (root / CITATION).read_text(encoding="utf-8"), re.MULTILINE)
    return {
        MANIFEST: json.loads((root / MANIFEST).read_text(encoding="utf-8")).get("."),
        "pyproject.toml": project.get("version"),
        INIT: init[1] if init else None,
        CITATION: cited[1] if cited else None,
        "uv.lock": lock.get("version"),
    }


def verdict(versions: dict[str, str | None], tag: str | None = None) -> tuple[bool, str]:
    missing = [name for name, version in versions.items() if version is None]
    if missing:
        return False, "No version found in " + ", ".join(missing) + "."
    found = set(versions.values())
    if len(found) > 1:
        listed = "; ".join(f"{name} {version}" for name, version in versions.items())
        return False, f"The versions differ: {listed}."
    version = found.pop()
    if tag is not None and tag != f"v{version}":
        return False, f"The tag {tag} doesn't match the version {version} in {', '.join(versions)}."
    also = f", and so does the tag {tag}" if tag is not None else ""
    return True, f"{', '.join(versions)} all say {version}{also}."


def main(argv: list[str]) -> int:
    args = argv[1:]
    if not args:
        tag = None
    elif len(args) == 2 and args[0] == "--tag":
        tag = args[1]
    else:
        print(__doc__, file=sys.stderr)
        return 1
    try:
        versions = read_versions(Path.cwd())
    except (OSError, ValueError) as error:
        print(f"Can't read the version files: {error}", file=sys.stderr)
        return 1
    ok, message = verdict(versions, tag)
    print(message, file=sys.stdout if ok else sys.stderr)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
