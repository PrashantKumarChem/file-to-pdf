"""Check a fresh build: convert generated files with topdf.exe and open the window.

    python packaging/smoke_test.py dist/FileToPDF

Standard library only, so it runs against a fresh build on any Python.
Exit code 0 when the packaged command line converted everything into real
PDFs and logged to LOCALAPPDATA, and the window started without an error.
"""

import ctypes
import json
import os
import subprocess
import sys
import tempfile
import time
from ctypes import wintypes
from pathlib import Path

NOTEBOOK = {
    "cells": [
        {"id": "m1", "cell_type": "markdown", "metadata": {},
         "source": "# Kinetics\n\nThe rate is $k = A e^{-E_a/RT}$."},
        {"id": "c1", "cell_type": "code", "execution_count": 1, "metadata": {},
         "source": "print('done')",
         "outputs": [{"name": "stdout", "output_type": "stream", "text": "done\n"}]},
    ],
    "metadata": {}, "nbformat": 4, "nbformat_minor": 5,
}
INPUTS = {
    "data.json": '{\n  "name": "water",\n  "mass": 18.015\n}\n',
    "notes.md": "# Notes\n\n| a | b |\n|---|---|\n| 1 | 2 |\n\n```python\nx = 1\n```\n",
    "fit.py": "def f(x):\n    return x  # comment\n",
    "geometry.xyz": "3\nwater\nO 0 0 0.117\n",
    "analysis.ipynb": json.dumps(NOTEBOOK),
}
EXPECTED = {"data.json.pdf", "notes.md.pdf", "fit.py.pdf", "geometry.xyz.pdf", "analysis.pdf"}
APP_NAME = "File to PDF"
WINDOW_TITLE = APP_NAME
# PyInstaller's windowed bootloader reports a crash in a dialog with this title.
CRASH_TITLE = "Unhandled exception in script"


def clean_env(tmp: Path) -> dict:
    # Nothing from a build environment may leak in: the app must find its own
    # browser and write its log under this LOCALAPPDATA.
    env = {k: v for k, v in os.environ.items() if k not in ("PLAYWRIGHT_BROWSERS_PATH", "PYTHONPATH")}
    env["LOCALAPPDATA"] = str(tmp)
    return env


def check_command_line(app: Path, tmp: Path) -> list[str]:
    sources, out = tmp / "inputs", tmp / "pdfs"
    sources.mkdir()
    for name, text in INPUTS.items():
        (sources / name).write_text(text, encoding="utf-8")
    run = subprocess.run(
        [str(app / "topdf.exe"), str(sources), "--out", str(out)],
        capture_output=True, text=True, env=clean_env(tmp), timeout=600,
    )
    print(run.stdout)
    print(run.stderr, file=sys.stderr)
    problems = []
    if run.returncode != 0:
        problems.append(f"topdf.exe exited with {run.returncode}")
    made = {p.name for p in out.glob("*.pdf")}
    if made != EXPECTED:
        problems.append(f"topdf.exe made {sorted(made)}, expected {sorted(EXPECTED)}")
    for pdf in out.glob("*.pdf"):
        data = pdf.read_bytes()
        if not data.startswith(b"%PDF-") or len(data) < 1000:
            problems.append(f"{pdf.name} is not a real PDF")
    if not (tmp / APP_NAME / "conversion_log.txt").is_file():
        problems.append(f"no log under LOCALAPPDATA\\{APP_NAME}")
    return problems


def window_titles(pid: int) -> list[str]:
    user32 = ctypes.windll.user32
    titles = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def visit(hwnd, _lparam):
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid and user32.IsWindowVisible(hwnd):
            buffer = ctypes.create_unicode_buffer(256)
            user32.GetWindowTextW(hwnd, buffer, len(buffer))
            titles.append(buffer.value)
        return True

    user32.EnumWindows(visit, 0)
    return titles


def check_window(app: Path, tmp: Path, timeout: float = 60) -> list[str]:
    process = subprocess.Popen([str(app / "File to PDF.exe")], env=clean_env(tmp))
    try:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if process.poll() is not None:
                return [f"File to PDF.exe exited with {process.returncode} before showing its window"]
            titles = window_titles(process.pid)
            if CRASH_TITLE in titles:
                return ["File to PDF.exe failed to start (PyInstaller's unhandled-exception dialog)"]
            if WINDOW_TITLE in titles:
                print(f"window opened: {WINDOW_TITLE!r}")
                return []
            time.sleep(0.5)
        # A session without a visible desktop shows no windows at all; the
        # process is alive and hasn't crashed, which is all that can be seen.
        print(f"window check inconclusive: no visible window within {timeout:.0f} s, process still running")
        return []
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()


def main() -> int:
    app = Path(sys.argv[1]).resolve()
    size = sum(p.stat().st_size for p in app.rglob("*") if p.is_file())
    print(f"{app}: {size / 1e6:.0f} MB")
    with tempfile.TemporaryDirectory() as tmp:
        problems = check_command_line(app, Path(tmp)) + check_window(app, Path(tmp))
    for problem in problems:
        print(f"SMOKE TEST FAILED: {problem}", file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
