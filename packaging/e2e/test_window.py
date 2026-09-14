"""File to PDF.exe from the unzipped app, clicked through by script (pywinauto).

The window is Tk: UI Automation sees its widgets as unnamed panes with real
positions, so they are found by layout (the root's five rows: header, drop
zone, options, file list, buttons). The Open dialog is Windows' own and is
driven by its controls. Clicks are real mouse input, so this test belongs
on a CI runner, not on a desktop someone is using.
"""

import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest
from conftest import APP_NAME, CORPUS
from pywinauto import Desktop
from pywinauto.application import Application

CRASH_TITLE = "Unhandled exception in script"  # PyInstaller's windowed bootloader

# The whole screen as a PNG, saved to the path in the SCREENSHOT environment variable.
SCREENSHOT = """
Add-Type -AssemblyName System.Windows.Forms, System.Drawing
$area = [System.Windows.Forms.SystemInformation]::VirtualScreen
$bitmap = New-Object System.Drawing.Bitmap $area.Width, $area.Height
[System.Drawing.Graphics]::FromImage($bitmap).CopyFromScreen($area.Left, $area.Top, 0, 0, $bitmap.Size)
$bitmap.Save($env:SCREENSHOT, [System.Drawing.Imaging.ImageFormat]::Png)
"""


def rows(window):
    (container,) = [c for c in window.children() if c.element_info.class_name == "TkChild"]
    return sorted(container.children(), key=lambda c: c.rectangle().top)


def widgets(row):
    """A frame's widgets, top to bottom then left to right, without its own canvas or 1-pixel labels."""
    frame = row.rectangle()
    return sorted(
        (c for c in row.children() if c.rectangle() != frame and c.rectangle().width() > 5),
        key=lambda c: (c.rectangle().top, c.rectangle().left),
    )


def choose_in_dialog(folder: Path, names: list[str], timeout: float = 60) -> None:
    # Looked for among the desktop's top-level windows as Windows lists them (win32), which
    # include windows owned by another window, as this dialog is; then driven by its handle.
    found = Desktop(backend="win32").window(title="Choose files")
    dialog = Desktop(backend="uia").window(handle=found.wait("visible", timeout=timeout).handle)
    file_name = dialog.child_window(auto_id="1148", control_type="Edit")
    file_name.set_edit_text(str(folder))
    file_name.type_keys("{ENTER}")
    time.sleep(1)  # the dialog moves to the folder
    file_name.set_edit_text(" ".join(f'"{name}"' for name in names))
    file_name.type_keys("{ENTER}")
    found.wait_not("visible", timeout=timeout)


def wait_for_files(folder: Path, names: set[str], timeout: float = 180) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if names <= {p.name for p in folder.glob("*")}:
            return
        time.sleep(0.5)
    present = sorted(p.name for p in folder.glob("*")) if folder.is_dir() else "no folder"
    pytest.fail(f"expected {sorted(names)} in {folder} within {timeout:.0f} s; found {present}")


def crash_dialog_shown() -> bool:
    return Desktop(backend="win32").window(title=CRASH_TITLE).exists(timeout=0)


def record_screen(folder: Path) -> None:
    """Print the visible top-level windows and save a screenshot in folder, which CI uploads when a test fails."""
    for w in Desktop(backend="win32").windows(visible_only=True):
        print(f"window {w.window_text()!r}, class {w.class_name()}, process {w.process_id()}, at {w.rectangle()}")
    screenshot = folder / "screen.png"
    env = {**os.environ, "SCREENSHOT": str(screenshot)}
    subprocess.run(["powershell", "-NoProfile", "-Command", SCREENSHOT], env=env, check=False)
    print(f"screenshot: {screenshot}" if screenshot.is_file() else "no screenshot")


def test_the_window_converts_files_chosen_in_its_dialog(app, sandbox):
    sources = sandbox.root / "sources"
    sources.mkdir()
    for name in ("data.json", "notes.md", "Überprüfung.ipynb"):
        shutil.copy(CORPUS / name, sources / name)
    shutil.copytree(CORPUS / "assets", sources / "assets")

    process = subprocess.Popen([str(app / f"{APP_NAME}.exe")], env=sandbox.env)
    try:
        window = (
            Application(backend="uia")
            .connect(process=process.pid, timeout=90)
            .window(title=APP_NAME, control_type="Window")
        )
        window.wait("visible", timeout=90)
        window.set_focus()
        _header, drop_zone, options, _file_list, buttons = rows(window)
        option_widgets = widgets(options)  # save to default, next to source, save to..., open folder when done
        assert len(option_widgets) == 4, [w.rectangle() for w in option_widgets]
        next_to_source, open_folder_when_done = option_widgets[1], option_widgets[3]

        # No Explorer windows: this run checks the files itself.
        open_folder_when_done.click_input()

        # Clicking the drop zone opens the Open dialog; the default output folder is used.
        drop_zone.click_input()
        choose_in_dialog(sources, ["data.json", "notes.md"])
        wait_for_files(sandbox.default_output, {"data.json.pdf", "notes.md.pdf"})

        # "Save next to source", then the Select file(s) button.
        next_to_source.click_input()
        widgets(buttons)[0].click_input()
        choose_in_dialog(sources, ["Überprüfung.ipynb"])
        wait_for_files(sources, {"Überprüfung.pdf"})
        assert not (sandbox.default_output / "Überprüfung.pdf").exists()

        assert not crash_dialog_shown()
        window.close()
        assert process.wait(timeout=60) == 0
    except BaseException:
        record_screen(sandbox.root)
        raise
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
    assert sandbox.log.is_file()
