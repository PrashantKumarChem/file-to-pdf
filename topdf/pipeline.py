"""
Conversion pipeline: file in, PDF saved, log written.

converters.py prints the file: notebooks (.ipynb) through nbconvert's HTML
exporter with the bundled topdf-notebook template, so long code lines and
output wrap instead of getting clipped off the page edge; JSON, Markdown,
code and plain text through their own adapters. One Chromium engine prints
them all. This module names the PDF, writes it with retries and a fallback
folder, and keeps the log.

Nothing here imports tkinter, so the pipeline runs and tests without a window.
"""

import os
import time
import traceback
from datetime import datetime
from pathlib import Path
from typing import NamedTuple

from topdf import APP_NAME, converters

# The log lives in the user's local application data: the code may sit in a
# folder that isn't writable (Program Files, the packaged app), and the log
# records the full path of every converted file, which doesn't belong next to
# the code.
LOG_DIR = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / APP_NAME
LOG_PATH = LOG_DIR / "conversion_log.txt"
LOG_MAX_BYTES = 1_000_000
# Default output: a plain folder directly under the user profile, outside the
# Documents folder that sync clients often manage and that can then refuse new
# files. The user can pick another folder; one that refuses the write sends
# the PDF to FALLBACK_DIR.
DEFAULT_OUTPUT_DIR = Path.home() / APP_NAME
FALLBACK_DIR = Path.home() / "Desktop" / f"{APP_NAME} - could not save"


class Result(NamedTuple):
    """Outcome of one conversion: saved_path is where the PDF landed, or None."""

    ok: bool
    status: str
    log: str
    saved_path: Path | None


def _log_header(path: Path) -> str:
    return f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {path}\n"


def convert_file(src_path: Path, output_dir: Path) -> Result:
    """Convert one file (notebook, JSON, Markdown, code or text) and save its PDF.

    The PDF is printed in memory and only then written into output_dir, with
    the retry and fallback logic of _save_pdf_bytes. If the page could not
    load some web resources (a notebook's MathJax or chart scripts when
    offline), the PDF is still saved and the status starts with a warning.
    """
    log_chunks = [_log_header(src_path)]
    try:
        rendered = converters.render(src_path)
    except Exception as e:  # noqa: BLE001 - surface any adapter/engine failure
        full_log = "".join(log_chunks) + "CONVERSION ERROR:\n" + traceback.format_exc()
        _append_log(full_log)
        return Result(False, f"Failed: {e}", full_log, None)
    # A notebook's PDF is named after the notebook (analysis.pdf); other files
    # keep their extension (analysis.json.pdf), so a notebook and a same-named
    # data file don't both collapse to analysis.pdf.
    stem = src_path.stem if converters.kind_for(src_path) == "Notebook" else src_path.name
    failed = rendered.failed_requests
    if failed:
        log_chunks.append("web resources that did not load:\n" + "".join(f"  {url}\n" for url in failed))
    result = _save_pdf_bytes(rendered.pdf, stem, output_dir, log_chunks, src_path)
    if result.ok and failed:
        noun = "resource" if len(failed) == 1 else "resources"
        warning = f"Warning: {len(failed)} web {noun} did not load; charts or math may be missing. "
        result = result._replace(status=warning + result.status)
    return result


def convert_any(path: Path, output_dir: Path) -> Result:
    """Convert one file and save its PDF. Never raises.

    The GUI runs every conversion on one worker thread. An exception escaping
    from here would end that thread and leave each later file queued forever,
    so anything unexpected becomes a failed Result carrying the traceback.
    """
    try:
        return convert_file(path, output_dir)
    except Exception as e:  # noqa: BLE001 - the worker must survive any failure
        full_log = _log_header(path) + "UNEXPECTED ERROR:\n" + traceback.format_exc()
        _append_log(full_log)
        return Result(False, f"Failed: {e}", full_log, None)


# Which source produced each PDF written by this process, keyed by normalized
# path. Re-converting a file replaces its own PDF; a different file whose PDF
# name collides gets "name (2).pdf" rather than silently replacing the first.
# Files from earlier sessions aren't tracked: an existing PDF of the same name
# is replaced, and the status says so.
_source_of_pdf: dict[str, str] = {}


def _path_key(path: Path) -> str:
    return os.path.normcase(os.path.abspath(path))


def _pdf_destination(folder: Path, stem: str, source: Path) -> Path:
    n = 1
    while True:
        dest = folder / (f"{stem}.pdf" if n == 1 else f"{stem} ({n}).pdf")
        owner = _source_of_pdf.get(_path_key(dest))
        if owner is None or owner == _path_key(source):
            return dest
        n += 1


def _write_pdf(dest: Path, data: bytes, source: Path) -> None:
    with open(dest, "wb") as f:
        f.write(data)
    _source_of_pdf[_path_key(dest)] = _path_key(source)


def _save_pdf_bytes(
    data: bytes, stem: str, output_dir: Path, log_chunks: list, source: Path
) -> Result:
    """Write PDF bytes into output_dir, retrying, then falling back.

    Some folders intermittently refuse new-file creation (cloud-synced ones
    can fail with FileNotFoundError even though the directory lists and stats
    fine), so the write is retried and, if output_dir keeps refusing, the PDF
    goes to a fallback folder so a good PDF is never lost.

    Raw byte write (open 'wb') rather than shutil.copy2: copy2 also sets file
    metadata (os.utime), a second step that can fail on such folders even
    after the content was written. Bytes only is the most compatible operation.
    """
    dest = _pdf_destination(output_dir, stem, source)
    # Checked once, before any attempt: a failed partial write must not turn
    # the next attempt into a "replaced".
    replaced = dest.exists()
    last_error = None
    for attempt in range(4):
        try:
            if not output_dir.exists():
                output_dir.mkdir(parents=True, exist_ok=True)
            _write_pdf(dest, data, source)
            last_error = None
            break
        except OSError as e:
            last_error = e
            log_chunks.append(f"copy attempt {attempt + 1} to {dest} failed: {e}\n")
            time.sleep(0.6 * (attempt + 1))

    if last_error is None:
        verb = "Replaced" if replaced else "Saved to"
        full_log = "".join(log_chunks) + f"{verb} {dest}\n"
        _append_log(full_log)
        done = "Done, replaced existing PDF" if replaced else "Done"
        return Result(True, f"{done} -> {dest}", full_log, dest)

    fallback_dest = _pdf_destination(FALLBACK_DIR, stem, source)
    try:
        FALLBACK_DIR.mkdir(parents=True, exist_ok=True)
        _write_pdf(fallback_dest, data, source)
    except OSError as e:
        status = f"Failed: couldn't write {output_dir} or the fallback folder: {e}"
        full_log = "".join(log_chunks) + status + "\n"
        _append_log(full_log)
        return Result(False, status, full_log, None)
    status = f"Saved to fallback (couldn't write {output_dir}): {fallback_dest}"
    log_chunks.append(status + "\n")
    full_log = "".join(log_chunks)
    _append_log(full_log)
    return Result(True, status, full_log, fallback_dest)


def _append_log(text: str) -> None:
    # Keep one previous generation.
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        if LOG_PATH.stat().st_size > LOG_MAX_BYTES:
            LOG_PATH.replace(LOG_PATH.with_name(f"{LOG_PATH.stem}.old{LOG_PATH.suffix}"))
    except OSError:
        pass  # no log yet, or it is locked; appending below still works
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(text + "\n" + ("=" * 80) + "\n")
    except OSError:
        pass
