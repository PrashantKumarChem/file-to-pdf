# PyInstaller spec for the Windows app that runs without a Python install.
#
# Build from the repository root (PowerShell):
#     pip install -r requirements.txt -r packaging/requirements.txt
#     $env:PLAYWRIGHT_BROWSERS_PATH = "0"; python -m playwright install --only-shell chromium
#     python -m PyInstaller --noconfirm packaging/topdf.spec
#     python packaging/smoke_test.py dist/FileToPDF
#
# dist/FileToPDF/ then holds two programs sharing one runtime:
#     File to PDF.exe   the window (topdf.gui)
#     topdf.exe         the command line (topdf.cli)
#
# PLAYWRIGHT_BROWSERS_PATH=0 installs Chromium's headless shell inside the
# Playwright package, so it is collected with the package's other data;
# frozen, Playwright looks there by itself.

import os
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
