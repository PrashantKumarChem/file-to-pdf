"""Fail when a change lowers the test coverage floor.

    python ci/coverage_floor.py [BASE_REV]

The floor is fail_under in pyproject.toml's [tool.coverage.report], and
"coverage report" fails when the total drops below it. This script guards
the floor itself: compared with pyproject.toml at BASE_REV (default HEAD^1,
which in a pull request's merge commit is the base branch), it may stay or
rise, never fall or disappear. Exit code 0 when it holds, 1 when it doesn't
or when BASE_REV can't be read.
"""

import subprocess
import sys
import tomllib
from pathlib import Path


def floor_in(pyproject_text: str) -> float | None:
    report = tomllib.loads(pyproject_text).get("tool", {}).get("coverage", {}).get("report", {})
    value = report.get("fail_under")
    return None if value is None else float(value)


def verdict(current: float | None, base: float | None) -> tuple[bool, str]:
    if base is None:
        if current is None:
            return True, "No coverage floor here or on the base."
        return True, f"The coverage floor is set at {current:g}% (the base had none)."
    if current is None:
        return False, f"The coverage floor ({base:g}% on the base) was removed; it may only rise."
    if current < base:
        return False, f"The coverage floor was lowered from {base:g}% to {current:g}%; it may only rise."
    if current > base:
        return True, f"The coverage floor rose from {base:g}% to {current:g}%."
    return True, f"The coverage floor stays at {current:g}%."


def main(argv: list[str]) -> int:
    base_rev = argv[1] if len(argv) > 1 else "HEAD^1"
    shown = subprocess.run(["git", "show", f"{base_rev}:pyproject.toml"], capture_output=True, encoding="utf-8")
    if shown.returncode != 0:
        print(f"Can't read pyproject.toml at {base_rev}: {shown.stderr.strip()}", file=sys.stderr)
        return 1
    ok, message = verdict(floor_in(Path("pyproject.toml").read_text(encoding="utf-8")), floor_in(shown.stdout))
    print(message, file=sys.stdout if ok else sys.stderr)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
