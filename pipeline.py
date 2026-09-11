"""
Conversion pipeline for the File -> PDF tool: file in, PDF saved, log written.

Notebooks (.ipynb) go through the nbconvert webpdf pipeline with the custom
'pdf-nowrap-fix' template (nbconvert-templates/pdf-nowrap-fix next to this
file) so long code lines and output wrap instead of getting clipped off the
page edge. Other formats (JSON,
Markdown, code, plain text/logs) are handled by converters.py, which renders
them to styled HTML and prints them through the same Chromium engine. One PDF
engine, many small adapters.

Nothing here imports tkinter, so the pipeline runs and tests without a window.
"""

import os
import subprocess
import sys
import tempfile
import time
import traceback
from datetime import datetime
from pathlib import Path
from typing import NamedTuple

import converters

# nbconvert runs in a child process of the interpreter running this code. The
# launcher starts the GUI with pythonw.exe; the child uses its console sibling
# python.exe (its window is suppressed by CREATE_NO_WINDOW).
_CONSOLE_PYTHON = Path(sys.executable).with_name("python.exe")
PYTHON_EXE = str(_CONSOLE_PYTHON if _CONSOLE_PYTHON.exists() else Path(sys.executable))
TEMPLATE_BASE_DIR = str(Path(__file__).parent / "nbconvert-templates")
TEMPLATE_NAME = "pdf-nowrap-fix"
LOG_PATH = Path(__file__).parent / "conversion_log.txt"
LOG_MAX_BYTES = 1_000_000
STAGING_DIR = Path(__file__).parent / "_staging"
# Default output: a plain folder directly under the user profile, which is
# reliably writable. On this machine a whole set of locations are currently
# cloud-sync-managed and refusing new-file creation (the D: drive, the
# G:\My Drive mount, AND the Documents folder -- the last one even
# carries a ReadOnly attribute). The bare profile root and Desktop are NOT
# managed and write fine, so we default there and the user can override.
DEFAULT_OUTPUT_DIR = Path.home() / "Notebook PDFs"
FALLBACK_DIR = Path.home() / "Desktop" / "NotebookToPDF - could not save"


NBCONVERT_TIMEOUT_S = 600


class Result(NamedTuple):
    """Outcome of one conversion: saved_path is where the PDF landed, or None."""

    ok: bool
    status: str
    log: str
    saved_path: Path | None


def _log_header(path: Path) -> str:
    return f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {path}\n"


def convert_notebook(nb_path: Path, output_dir: Path) -> Result:
    """Convert a notebook with nbconvert's webpdf exporter and save the PDF.

    nbconvert always writes to a local staging directory first, then the
    finished PDF is copied into the requested output folder. Writing
    directly into the requested folder turned out to be unreliable: some
    volumes on this machine (the D: partition, and the G: Google Drive
    mount) currently refuse ALL new-file creation, failing with
    FileNotFoundError even though the directory lists and stats fine.
    Keeping nbconvert's own write on local staging (C:) means a successful
    conversion is never at the mercy of that, and only the final copy needs
    retries / a fallback location. saved_path is where the PDF actually
    ended up (requested folder, or the fallback), or None on failure.
    """
    STAGING_DIR.mkdir(parents=True, exist_ok=True)
    log_chunks = [_log_header(nb_path)]

    with tempfile.TemporaryDirectory(dir=STAGING_DIR) as tmp_dir_str:
        tmp_dir = Path(tmp_dir_str)
        cmd = [
            PYTHON_EXE, "-m", "nbconvert",
            "--to", "webpdf",
            "--template", TEMPLATE_NAME,
            f"--TemplateExporter.extra_template_basedirs={TEMPLATE_BASE_DIR}",
            "--output", nb_path.stem,
            "--output-dir", str(tmp_dir),
            str(nb_path),
        ]
        log_chunks.append(f"cmd: {cmd}\n")
        try:
            # The child writes its console streams in the locale code page
            # unless told otherwise; pin both ends to UTF-8 so a non-ASCII
            # notebook name or output can't fail the decode.
            result = subprocess.run(
                cmd, capture_output=True, encoding="utf-8", errors="replace",
                env={**os.environ, "PYTHONIOENCODING": "utf-8"},
                timeout=NBCONVERT_TIMEOUT_S,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        except subprocess.TimeoutExpired:
            message = f"timed out after {NBCONVERT_TIMEOUT_S // 60} minutes"
            full_log = "".join(log_chunks) + message.upper() + "\n"
            _append_log(full_log)
            return Result(False, f"Failed: {message}", full_log, None)
        except OSError as e:
            full_log = "".join(log_chunks) + f"could not start nbconvert: {e}\n"
            _append_log(full_log)
            return Result(False, f"Failed: could not start nbconvert: {e}", full_log, None)

        log_chunks.append(
            f"return code: {result.returncode}\n"
            f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}\n"
        )

        if result.returncode != 0:
            full_log = "".join(log_chunks)
            _append_log(full_log)
            lines = [l for l in result.stderr.strip().splitlines() if l.strip()]
            short = lines[-1] if lines else "Unknown error (see log)"
            return Result(False, f"Failed: {short}", full_log, None)

        produced = tmp_dir / f"{nb_path.stem}.pdf"
        if not produced.exists():
            full_log = "".join(log_chunks) + f"nbconvert reported success but {produced} is missing\n"
            _append_log(full_log)
            return Result(False, "Failed: PDF missing after conversion (see log)", full_log, None)

        return _save_pdf_bytes(produced.read_bytes(), nb_path.stem, output_dir, log_chunks, nb_path)


def convert_file(src_path: Path, output_dir: Path) -> Result:
    """Convert a non-notebook file (JSON/Markdown/code/text) to PDF.

    Renders the file to styled HTML via converters.py, then prints it to PDF
    with the shared Chromium engine, and saves it with the same retry/fallback
    logic as notebooks.
    """
    log_chunks = [_log_header(src_path)]
    try:
        pdf_bytes = converters.file_to_pdf_bytes(src_path)
    except Exception as e:  # noqa: BLE001 - surface any adapter/engine failure
        full_log = "".join(log_chunks) + "CONVERSION ERROR:\n" + traceback.format_exc()
        _append_log(full_log)
        return Result(False, f"Failed: {e}", full_log, None)
    # Keep the source extension in the PDF name (e.g. Compound1.json.pdf) so a
    # notebook and a same-named data file don't both collapse to Compound1.pdf.
    return _save_pdf_bytes(pdf_bytes, src_path.name, output_dir, log_chunks, src_path)


def convert_any(path: Path, output_dir: Path) -> Result:
    """Route a file to the right converter by extension. Never raises.

    The GUI runs every conversion on one worker thread. An exception escaping
    from here would end that thread and leave each later file queued forever,
    so anything unexpected becomes a failed Result carrying the traceback.
    """
    try:
        if path.suffix.lower() == ".ipynb":
            return convert_notebook(path, output_dir)
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

    Raw byte write (open 'wb') rather than shutil.copy2: copy2 also sets file
    metadata (os.utime), a second way to fail on the quirky volumes here --
    content-write can succeed while the metadata step raises. Bytes-only is the
    minimal, most-compatible operation. If output_dir keeps refusing the write,
    save to a guaranteed-writable fallback so a good PDF is never lost.
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
    # Keep one previous generation; every notebook adds nbconvert's full output.
    try:
        if LOG_PATH.stat().st_size > LOG_MAX_BYTES:
            LOG_PATH.replace(LOG_PATH.with_name(f"{LOG_PATH.stem}.old{LOG_PATH.suffix}"))
    except OSError:
        pass  # no log yet, or it is locked; appending below still works
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(text + "\n" + ("=" * 80) + "\n")
    except OSError:
        pass
