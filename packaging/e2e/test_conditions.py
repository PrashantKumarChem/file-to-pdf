"""The unzipped app under harder conditions: no network, long folder paths, a folder
that refuses writes, non-English names, and files that share a name."""

import ctypes
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from conftest import CORPUS, pdf_text, run_topdf

WINDOWS_PATH_LIMIT = 260  # Windows won't start a program whose path is this long or longer


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# No network
# ---------------------------------------------------------------------------
@pytest.fixture
def no_network(app):
    """Block outbound connections for every program in the app folder with Windows Firewall rules."""
    if not ctypes.windll.shell32.IsUserAnAdmin():
        if os.environ.get("CI"):
            pytest.fail("Blocking the network needs an elevated session, which CI runners have.")
        pytest.skip("Blocking the network needs administrator rights (run elevated to include this test).")
    group = f"topdf-e2e-no-network-{os.getpid()}"
    programs = sorted(app.rglob("*.exe"))
    assert any(p.name == "chrome-headless-shell.exe" for p in programs)
    quoted = [str(p).replace("'", "''") for p in programs]
    add = "; ".join(
        f"New-NetFirewallRule -DisplayName '{group}' -Group '{group}' -Direction Outbound -Action Block "
        f"-Program '{program}' | Out-Null"
        for program in quoted
    )
    subprocess.run(["powershell", "-NoProfile", "-Command", add], check=True, capture_output=True, text=True)
    try:
        yield
    finally:
        subprocess.run(
            ["powershell", "-NoProfile", "-Command", f"Remove-NetFirewallRule -Group '{group}'"],
            check=True,
            capture_output=True,
        )


def test_without_network_files_still_convert_and_say_what_did_not_load(app, sandbox, no_network):
    notebook = sandbox.root / "notebook.ipynb"
    shutil.copy(CORPUS / "notebook.ipynb", notebook)
    remote = write(sandbox.root / "remote.md", "# Remote figure\n\n![figure](https://example.com/figure.png)\n")
    out = sandbox.root / "out"

    run = run_topdf(app, sandbox, notebook, remote, "--out", out)

    assert run.returncode == 0
    # The notebook's page loads its script libraries from the web, the Markdown file its image.
    lines = [line for line in run.stdout.splitlines() if line.startswith("[")]
    assert len(lines) == 2
    assert all("did not load; charts or math may be missing. Done -> " in line for line in lines), lines
    assert "Esterification yield summary" in pdf_text(out / "notebook.pdf")[0]
    assert "Remote figure" in pdf_text(out / "remote.md.pdf")[0]


# ---------------------------------------------------------------------------
# Long folder paths
# ---------------------------------------------------------------------------
def folder_of_length(parent: Path, length: int) -> Path:
    """A path under parent that is exactly length characters long, in components Windows accepts."""
    remaining = length - len(str(parent)) - 1
    assert remaining > 0, f"{parent} is already {len(str(parent))} characters"
    path = parent
    while remaining > 0:
        part = min(remaining, 100)
        if remaining - part == 1:  # a lone separator would be left over
            part -= 2
        path = path / ("d" * part)
        remaining -= part + (1 if remaining - part else 0)
    assert len(str(path)) == length, (len(str(path)), length)
    return path


@pytest.fixture
def movable_app(app):
    """Moves the app folder where a test wants it, and back afterwards."""
    moved = []

    def move_to(target: Path) -> Path:
        target.parent.mkdir(parents=True, exist_ok=True)
        current = moved[-1] if moved else app
        shutil.move(current, target)
        moved.append(target)
        return target

    yield move_to
    if moved:
        shutil.move(moved[-1], app)


def headless_shell_depth(app: Path) -> int:
    (shell,) = app.rglob("chrome-headless-shell.exe")
    return len(str(shell)) - len(str(app))


def test_a_long_folder_path_up_to_the_limit_works(app, sandbox, movable_app):
    depth = headless_shell_depth(app)
    source = write(sandbox.root / "a.json", '{"a": 1}\n')
    # The headless shell's full path is 259 characters: the longest Windows starts.
    folder = movable_app(folder_of_length(sandbox.root / "fits", WINDOWS_PATH_LIMIT - 1 - depth))
    print(f"app folder: {len(str(folder))} characters; headless shell {depth} characters deeper")

    run = run_topdf(folder, sandbox, source, "--out", sandbox.root / "out")

    assert run.returncode == 0
    assert (sandbox.root / "out" / "a.json.pdf").is_file()


def test_a_folder_path_past_the_limit_fails_with_an_explanation(app, sandbox, movable_app):
    depth = headless_shell_depth(app)
    source = write(sandbox.root / "a.json", '{"a": 1}\n')
    folder = movable_app(folder_of_length(sandbox.root / "long", WINDOWS_PATH_LIMIT + 5 - depth))

    run = run_topdf(folder, sandbox, source, "--out", sandbox.root / "out")

    assert run.returncode == 1
    assert "past Windows' 260-character limit for starting programs" in run.stdout
    assert "move the app to a folder with a shorter path" in run.stdout


# ---------------------------------------------------------------------------
# A folder that refuses writes
# ---------------------------------------------------------------------------
def test_a_folder_that_refuses_writes_sends_the_pdf_to_the_fallback_folder(app, sandbox):
    locked = sandbox.root / "locked"
    locked.mkdir()
    user = subprocess.run(["whoami"], capture_output=True, text=True, check=True).stdout.strip()
    # Deny creating files and folders in it (WD, AD) rather than all of write (W): W includes
    # SYNCHRONIZE, which also stops the folder being listed, and the test lists it at the end.
    subprocess.run(["icacls", str(locked), "/deny", f"{user}:(OI)(CI)(WD,AD)"], check=True, capture_output=True)
    try:
        with pytest.raises(PermissionError):  # otherwise this test proves nothing
            (locked / "check.txt").write_text("x")
        source = write(sandbox.root / "a.json", '{"a": 1}\n')

        run = run_topdf(app, sandbox, source, "--out", locked)

        assert run.returncode == 0
        assert f"Saved to fallback (couldn't write {locked}): {sandbox.fallback / 'a.json.pdf'}" in run.stdout
        assert (sandbox.fallback / "a.json.pdf").is_file()
        assert not list(locked.iterdir())
    finally:
        subprocess.run(["icacls", str(locked), "/remove:d", user], check=True, capture_output=True)


# ---------------------------------------------------------------------------
# Names
# ---------------------------------------------------------------------------
NON_ENGLISH = {
    "Überprüfung.md": "# Überprüfung\n\nAusbeute 83 %\n",
    "データ.json": '{"収率": 83.4}\n',
    "ملاحظات.txt": "ملاحظات المختبر\n",
    "χημεία.py": 'print("χημεία")\n',
}


def test_non_english_file_and_folder_names(app, sandbox):
    folder = sandbox.root / "Проект データ"
    for name, text in NON_ENGLISH.items():
        write(folder / name, text)
    shutil.copy(CORPUS / "Überprüfung.ipynb", folder / "реакция.ipynb")
    out = sandbox.root / "Ausgabe ü"

    run = run_topdf(app, sandbox, folder, "--out", out)

    assert run.returncode == 0
    expected = sorted([*(f"{name}.pdf" for name in NON_ENGLISH), "реакция.pdf"])
    assert sorted(p.name for p in out.glob("*.pdf")) == expected
    assert "in Ordnung" in pdf_text(out / "реакция.pdf")[0]

    run = run_topdf(app, sandbox, folder / "データ.json", "--next-to-source")
    assert run.returncode == 0
    assert (folder / "データ.json.pdf").is_file()


def test_same_named_files_get_their_own_pdfs(app, sandbox):
    one = write(sandbox.root / "one" / "data.json", '{"from": "one"}\n')
    two = write(sandbox.root / "two" / "data.json", '{"from": "two"}\n')
    notebook = sandbox.root / "one" / "analysis.ipynb"
    shutil.copy(CORPUS / "Überprüfung.ipynb", notebook)
    analysis = write(sandbox.root / "one" / "analysis.json", '{"kind": "analysis data"}\n')
    out = sandbox.root / "out"

    run = run_topdf(app, sandbox, one, two, notebook, analysis, "--out", out)

    assert run.returncode == 0
    names = sorted(p.name for p in out.glob("*.pdf"))
    assert names == ["analysis.json.pdf", "analysis.pdf", "data.json (2).pdf", "data.json.pdf"]
    assert '"one"' in pdf_text(out / "data.json.pdf")[0]
    assert '"two"' in pdf_text(out / "data.json (2).pdf")[0]
    assert "analysis data" in pdf_text(out / "analysis.json.pdf")[0]
