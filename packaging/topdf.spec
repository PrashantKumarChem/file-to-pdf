# PyInstaller spec for the Windows app that runs without a Python install.
#
# Build from the repository root (PowerShell):
#     pip install -r requirements.txt -r packaging/requirements.txt
#     $env:PLAYWRIGHT_BROWSERS_PATH = "0"; python -m playwright install --only-shell chromium
#     python -m PyInstaller --noconfirm packaging/topdf.spec
#     python packaging/smoke_test.py dist/FileToPDF
#
# dist/FileToPDF/ then holds two programs sharing one runtime:
#     File to PDF.exe            the window (topdf.gui)
#     topdf.exe                  the command line (topdf.cli)
#     LICENSE.txt                the app's license
#     THIRD-PARTY-NOTICES.txt    the licenses of everything bundled
#
# PLAYWRIGHT_BROWSERS_PATH=0 installs Chromium's headless shell inside the
# Playwright package, so it is collected with the package's other data;
# frozen, Playwright looks there by itself.

import importlib.metadata
import os
import platform
import re
import shutil
import sys
from pathlib import Path

import playwright
from PyInstaller.utils.hooks import collect_submodules

# PyInstaller finds the DLLs that extension modules load by searching PATH.
# Any other folder there that ships its own copies (Graphviz carries
# libexpat.dll and an older tcl86t.dll) gets bundled instead of the
# interpreter's, and the app then fails at start: pyexpat won't load and Tk
# finds a Tcl of the wrong version. Search only the interpreter's own folders
# (conda keeps its DLLs in Library\bin) and Windows.
_base = Path(sys.base_prefix)
_windows = Path(os.environ["SystemRoot"])
os.environ["PATH"] = os.pathsep.join(
    str(p) for p in (_base, _base / "DLLs", _base / "Library" / "bin", _windows / "System32", _windows)
    if p.is_dir()
)

HERE = Path(SPECPATH)
ROOT = HERE.parent
# nbconvert's own templates (lab, base) are installed outside site-packages;
# the frozen jupyter_core looks for them under sys.prefix/share/jupyter.
STOCK_TEMPLATES = Path(sys.prefix) / "share" / "jupyter" / "nbconvert" / "templates"
BROWSERS = Path(playwright.__file__).parent / "driver" / "package" / ".local-browsers"

if not (STOCK_TEMPLATES / "lab").is_dir():
    sys.exit(f"nbconvert templates not found at {STOCK_TEMPLATES}")
if not any(BROWSERS.glob("chromium_headless_shell-*")):
    sys.exit(f"no headless shell in {BROWSERS}: install it with PLAYWRIGHT_BROWSERS_PATH=0 first")
if any(BROWSERS.glob("chromium-*")):
    sys.exit(f"full Chromium found in {BROWSERS}: install with --only-shell to keep the app small")

# Entry script in this folder -> program name. Each script only calls into the
# topdf package, which is collected from the repository root.
ENTRY_SCRIPTS = {"launch_window": "File to PDF", "launch_cli": "topdf"}

# One analysis for both programs: they import the same modules, and two
# separate analyses held two full dependency graphs in memory at once.
analysis = Analysis(
    [str(HERE / f"{script}.py") for script in ENTRY_SCRIPTS],
    pathex=[str(ROOT)],
    datas=[
        # converters.py loads the notebook template from next to itself.
        (str(ROOT / "topdf" / "templates"), "topdf/templates"),
        (str(STOCK_TEMPLATES), "share/jupyter/nbconvert/templates"),
    ],
    # Markdown loads its extensions by name at runtime.
    hiddenimports=collect_submodules("markdown.extensions"),
    excludes=["fitz", "pymupdf", "pytest", "psutil"],
)

# Playwright installs ffmpeg next to Chromium but only runs it to record
# video, which the app never does. Leaving it out saves 3 MB and keeps its
# LGPL binary out of the app. (winldd stays: Playwright uses it to explain
# why a browser failed to start.)
_UNUSED = re.compile(r"(^|[\\/])\.local-browsers[\\/]ffmpeg-")
analysis.datas = [entry for entry in analysis.datas if not _UNUSED.search(entry[0])]
analysis.binaries = [entry for entry in analysis.binaries if not _UNUSED.search(entry[0])]

pyz = PYZ(analysis.pure)


def scripts_for(script):
    # analysis.scripts holds PyInstaller's bootstrap and runtime hooks followed
    # by every entry script; each program keeps the hooks and its own script.
    others = set(ENTRY_SCRIPTS) - {script}
    return [entry for entry in analysis.scripts if entry[0] not in others]


window_exe = EXE(
    pyz, scripts_for("launch_window"), [],
    exclude_binaries=True, name=ENTRY_SCRIPTS["launch_window"], console=False, upx=False,
)
command_line_exe = EXE(
    pyz, scripts_for("launch_cli"), [],
    exclude_binaries=True, name=ENTRY_SCRIPTS["launch_cli"], console=True, upx=False,
)
COLLECT(
    window_exe, command_line_exe, analysis.binaries, analysis.datas,
    name="FileToPDF", upx=False,
)

# License notices. COLLECT has filled the app folder by now; the notices are
# written next to the programs so they ship in the zip.
APP = Path(DISTPATH) / "FileToPDF"
_LICENSE_NAME = re.compile(r"(LICEN[CS]E|COPYING|NOTICE)", re.IGNORECASE)


def bundled_distributions():
    # Map every collected file back to the installed package that owns it, so
    # the notices cover exactly the packages that ship, however they got in.
    owners = {}
    for dist in importlib.metadata.distributions():
        for file in dist.files or []:
            owners[os.path.normcase(os.path.abspath(dist.locate_file(file)))] = dist
    found = {}
    for toc in (analysis.scripts, analysis.pure, analysis.binaries, analysis.datas):
        for _dest, source, _typecode in toc:
            if isinstance(source, str) and source:
                dist = owners.get(os.path.normcase(os.path.abspath(source)))
                if dist is not None:
                    found[dist.metadata["Name"].lower()] = dist
    return [found[name] for name in sorted(found)]


def license_texts(dist):
    # Wheels keep license files in .dist-info/licenses/ (PEP 639) or, in
    # older ones, at the top of .dist-info.
    texts = []
    for file in dist.files or []:
        parts = file.parts
        if not parts[0].endswith(".dist-info"):
            continue
        if (len(parts) >= 3 and parts[1] == "licenses") or (len(parts) == 2 and _LICENSE_NAME.match(file.name)):
            texts.append((file.name, Path(dist.locate_file(file)).read_text(encoding="utf-8", errors="replace")))
    return texts


def license_label(dist):
    metadata = dist.metadata
    expression = metadata.get("License-Expression")
    if expression:
        return expression
    classifiers = [c.split("::")[-1].strip() for c in metadata.get_all("Classifier") or [] if c.startswith("License ::")]
    first_line = (metadata.get("License") or "").strip().splitlines()[:1]
    return ", ".join(classifiers) or (first_line[0] if first_line else "see the license text below")


def python_license():
    # python.org builds keep LICENSE.txt at the prefix; conda names it
    # LICENSE_PYTHON.txt.
    for name in ("LICENSE.txt", "LICENSE_PYTHON.txt"):
        if (_base / name).is_file():
            return (_base / name).read_text(encoding="utf-8", errors="replace")
    sys.exit(f"no Python license file in {_base}")


def rule(title, char="="):
    return f"{title}\n{char * len(title)}\n"


sections = [
    rule("Third-party software in File to PDF"),
    "File to PDF is licensed under the MIT License (LICENSE.txt). It bundles the\n"
    "software below, each under its own license.\n",
    rule(f"Python {platform.python_version()}"),
    python_license(),
]

# Components that carry their own license files inside the app folder:
# Chromium, the Node.js runtime Playwright runs on, Tcl/Tk, and files vendored
# inside packages. Their texts are large, so they are referenced, not copied.
in_folder = sorted(
    path.relative_to(APP) for path in APP.rglob("*")
    if path.is_file() and _LICENSE_NAME.match(path.name) and not any(p.endswith(".dist-info") for p in path.parts)
)
sections.append(rule("License files inside this folder"))
sections.append("\n".join(str(path) for path in in_folder) + "\n")

sections.append(rule("Python packages"))
missing = []
for dist in bundled_distributions():
    texts = license_texts(dist)
    if not texts:
        missing.append(f"{dist.metadata['Name']} {dist.version}")
        continue
    sections.append(rule(f"{dist.metadata['Name']} {dist.version}", "-"))
    sections.append(f"License: {license_label(dist)}\n")
    for name, text in texts:
        sections.append(f"[{name}]\n{text.strip()}\n")
if missing:
    sys.exit("no license file found for bundled packages: " + ", ".join(missing))

shutil.copyfile(ROOT / "LICENSE", APP / "LICENSE.txt")
(APP / "THIRD-PARTY-NOTICES.txt").write_text("\n".join(sections), encoding="utf-8")
