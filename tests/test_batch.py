"""One browser per batch: sharing, cleanup, and recovery when Chromium or its driver dies."""

import contextlib
import subprocess
import sys
import textwrap
import threading
import time
from pathlib import Path

import pytest

fitz = pytest.importorskip("fitz")
psutil = pytest.importorskip("psutil")
pytest.importorskip("playwright")

import converters  # noqa: E402

pytestmark = pytest.mark.slow

REPO = Path(__file__).resolve().parents[1]


def page_text(data: bytes) -> str:
    return fitz.open(stream=data, filetype="pdf")[0].get_text()


def process_name(process) -> str:
    try:
        return process.name().lower()
    except psutil.Error:
        return ""


def playwright_processes(root=None):
    """The Playwright driver (node) and Chromium processes below a process."""
    root = root or psutil.Process()
    return [p for p in root.children(recursive=True) if any(n in process_name(p) for n in ("node", "chrome"))]


def gone(pids, timeout=15):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not any(psutil.pid_exists(pid) for pid in pids):
            return True
        time.sleep(0.2)
    return False


@pytest.fixture
def launches(monkeypatch):
    from playwright.async_api._generated import BrowserType

    calls = []
    real_launch = BrowserType.launch

    async def recording_launch(self, *args, **kwargs):
        calls.append(True)
        return await real_launch(self, *args, **kwargs)

    monkeypatch.setattr(BrowserType, "launch", recording_launch)
    return calls


def test_prints_in_a_batch_share_one_browser(launches):
    with converters.batch():
        for word in ("one", "two", "three"):
            assert word in page_text(converters.html_to_pdf(converters.wrap_html(f"<p>{word}</p>")))
    assert len(launches) == 1
    converters.html_to_pdf("<p>four</p>")
    converters.html_to_pdf("<p>five</p>")
    assert len(launches) == 3  # outside a batch, one browser per print


def test_a_batch_that_prints_nothing_starts_no_browser(launches):
    with converters.batch():
        pass
    assert launches == []


def test_nothing_outlives_a_batch():
    before = threading.active_count()
    with converters.batch():
        converters.html_to_pdf("<p>x</p>")
        pids = [p.pid for p in playwright_processes()]
        assert pids
    assert threading.active_count() == before
    assert gone(pids)


@pytest.mark.parametrize("victim", ["chrome", "node"])
def test_batch_recovers_when_chromium_or_its_driver_dies(victim):
    with converters.batch():
        converters.html_to_pdf("<p>before</p>")
        for process in playwright_processes():
            if victim in process_name(process):
                with contextlib.suppress(psutil.Error):
                    process.kill()
        time.sleep(0.5)
        assert "after" in page_text(converters.html_to_pdf(converters.wrap_html("<p>after</p>")))


def test_browser_processes_end_when_the_app_exits_mid_batch():
    # The GUI's worker is a daemon thread: closing the window can end the
    # process inside a batch with no cleanup at all. os._exit models that.
    child = textwrap.dedent(f"""
        import os, sys
        sys.path.insert(0, {str(REPO)!r})
        import psutil, converters
        with converters.batch():
            converters.html_to_pdf("<p>x</p>")
            print(" ".join(str(p.pid) for p in psutil.Process().children(recursive=True)), flush=True)
            os._exit(0)
    """)
    result = subprocess.run([sys.executable, "-c", child], capture_output=True, text=True, timeout=60)
    pids = [int(pid) for pid in result.stdout.split()]
    assert pids, result.stderr
    assert gone(pids)
