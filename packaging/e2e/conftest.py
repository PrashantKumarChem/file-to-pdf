"""End-to-end tests of the packaged app: the unzipped FileToPDF folder, never the source.

    uv run python -m pytest packaging/e2e --app path/to/FileToPDF

Every test runs the app's own programs in a sandbox: USERPROFILE and
LOCALAPPDATA point into the test's temporary folder, so the default output
folder, the fallback folder and the log are the test's own, and nothing from
the environment that built or tests the app (PLAYWRIGHT_BROWSERS_PATH,
PYTHONPATH, a virtual environment) reaches it.
"""

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import cast

import pymupdf
import pytest

REPO = Path(__file__).resolve().parents[2]
CORPUS = REPO / "tests" / "visual" / "corpus"
APP_NAME = "File to PDF"


def pytest_addoption(parser):
    parser.addoption("--app", type=Path, help="the unzipped FileToPDF folder to test")


@pytest.fixture(scope="session")
def app(request) -> Path:
    folder = request.config.getoption("--app")
    if folder is None:
        pytest.fail("Give the unzipped app folder with --app.")
    folder = Path(folder).resolve()
    for program in ("topdf.exe", f"{APP_NAME}.exe"):
        if not (folder / program).is_file():
            pytest.fail(f"{folder} has no {program}")
    return folder


@dataclass
class Sandbox:
    root: Path
    home: Path
    env: dict[str, str]

    @property
    def default_output(self) -> Path:
        return self.home / APP_NAME

    @property
    def fallback(self) -> Path:
        return self.home / "Desktop" / f"{APP_NAME} - could not save"

    @property
    def log(self) -> Path:
        return Path(self.env["LOCALAPPDATA"]) / APP_NAME / "conversion_log.txt"


@pytest.fixture
def sandbox(tmp_path) -> Sandbox:
    home = tmp_path / "home"
    (home / "Desktop").mkdir(parents=True)
    local_app_data = tmp_path / "LocalAppData"
    local_app_data.mkdir()
    leaks = {"PLAYWRIGHT_BROWSERS_PATH", "PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV"}
    env = {key: value for key, value in os.environ.items() if key.upper() not in leaks}
    env.update(USERPROFILE=str(home), LOCALAPPDATA=str(local_app_data))
    return Sandbox(tmp_path, home, env)


def run_topdf(app: Path, sandbox: Sandbox, *args, timeout: float = 300) -> subprocess.CompletedProcess:
    """Run the packaged topdf.exe. Its output is only checked for ASCII text (file names are checked on disk)."""
    run = subprocess.run(
        [str(app / "topdf.exe"), *(str(arg) for arg in args)],
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        env=sandbox.env,
        timeout=timeout,
    )
    print(f"exit code {run.returncode}\n--- stdout\n{run.stdout}--- stderr\n{run.stderr}")
    return run


def pdf_text(path: Path) -> tuple[str, int]:
    """A PDF's text with whitespace collapsed, and its page count."""
    with pymupdf.open(path) as doc:
        return " ".join(" ".join(cast(str, page.get_text()).split()) for page in doc), len(doc)
