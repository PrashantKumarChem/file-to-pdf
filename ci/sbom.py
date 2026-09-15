"""Write the Windows app's software bill of materials, in CycloneDX 1.5 JSON.

    uv run --no-sync python ci/sbom.py LOCK_SBOM OUTPUT

LOCK_SBOM is uv's export of uv.lock for the app ("uv export --no-dev --format
cyclonedx1.5"): the packages the app depends on, with PyPI's hashes, for every
platform uv.lock covers. Run with the Python of the environment the app was
built from, this keeps the packages installed there, which PyInstaller bundles,
and adds what the zip carries that uv.lock doesn't list: CPython with its
Tcl/Tk, PyInstaller's bootloader, and the Chromium headless shell Playwright
installed into its package.

Exit code 0 when OUTPUT was written, 1 when an input is missing or unreadable.
"""

import copy
import importlib.metadata
import json
import platform
import re
import sys
from pathlib import Path

SOURCE = "file-to-pdf:source"


def normalized(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def installed_names() -> set[str]:
    return {normalized(dist.metadata["Name"]) for dist in importlib.metadata.distributions()}


def component(kind: str, name: str, version: str, purl: str, source: str) -> dict:
    return {
        "type": kind,
        "bom-ref": f"{name}@{version}",
        "name": name,
        "version": version,
        "purl": purl,
        "properties": [{"name": SOURCE, "value": source}],
    }


def bundled_components(python: str, tk: str, pyinstaller: str, chromium: str) -> list[dict]:
    """What the zip carries besides the packages in uv.lock."""
    return [
        component("application", "cpython", python, f"pkg:generic/cpython@{python}", "the build's Python, bundled"),
        component("library", "tcl-tk", tk, f"pkg:generic/tcl-tk@{tk}", "CPython's Tcl/Tk, bundled for the window"),
        component(
            "application",
            "pyinstaller-bootloader",
            pyinstaller,
            f"pkg:pypi/pyinstaller@{pyinstaller}",
            "PyInstaller's bootloader, which starts topdf.exe",
        ),
        component(
            "application",
            "chromium-headless-shell",
            chromium,
            f"pkg:generic/chromium-headless-shell@{chromium}",
            "Playwright's Chromium headless shell, which prints the PDFs",
        ),
    ]


def measured_components() -> list[dict]:
    """bundled_components, with the versions of this environment."""
    import tkinter

    import playwright

    package = Path(playwright.__file__).parent / "driver" / "package"
    browsers = json.loads((package / "browsers.json").read_text(encoding="utf-8"))["browsers"]
    shell = next(browser for browser in browsers if browser["name"] == "chromium-headless-shell")
    installed = package / ".local-browsers" / f"chromium_headless_shell-{shell['revision']}"
    if not installed.is_dir():
        raise FileNotFoundError(f"the headless shell isn't installed at {installed}")
    return bundled_components(
        python=platform.python_version(),
        tk=tkinter.Tcl().eval("info patchlevel"),
        pyinstaller=importlib.metadata.version("pyinstaller"),
        chromium=shell["browserVersion"],
    )


def build_sbom(lock_sbom: dict, installed: set[str], bundled: list[dict]) -> tuple[dict, list[str]]:
    """The bill of materials, and the names of the lock's packages left out as not installed."""
    sbom = copy.deepcopy(lock_sbom)
    kept = []
    dropped_refs = set()
    dropped_names = []
    for entry in sbom.get("components", []):
        if normalized(entry["name"]) in installed:
            kept.append(entry)
        else:
            dropped_refs.add(entry["bom-ref"])
            dropped_names.append(entry["name"])
    sbom["components"] = kept + bundled
    dependencies = []
    for dependency in sbom.get("dependencies", []):
        if dependency["ref"] in dropped_refs:
            continue
        dependency["dependsOn"] = [ref for ref in dependency.get("dependsOn", []) if ref not in dropped_refs]
        dependencies.append(dependency)
    sbom["dependencies"] = dependencies
    return sbom, sorted(dropped_names)


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(__doc__, file=sys.stderr)
        return 1
    try:
        lock_sbom = json.loads(Path(argv[1]).read_text(encoding="utf-8"))
        bundled = measured_components()
    except (OSError, ValueError, KeyError, StopIteration, ImportError) as error:
        print(f"Can't write the bill of materials: {error!r}", file=sys.stderr)
        return 1
    sbom, dropped = build_sbom(lock_sbom, installed_names(), bundled)
    Path(argv[2]).write_text(json.dumps(sbom, indent=2) + "\n", encoding="utf-8", newline="\n")
    added = ", ".join(f"{entry['name']} {entry['version']}" for entry in bundled)
    print(f"{argv[2]}: {len(sbom['components'])} components. Added: {added}.")
    print("Left out, not installed in the build environment: " + (", ".join(dropped) or "none") + ".")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
