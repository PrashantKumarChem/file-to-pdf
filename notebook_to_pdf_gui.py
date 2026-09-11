"""
File -> PDF: pick files and get PDFs out.

Notebooks (.ipynb) go through the nbconvert webpdf pipeline with the custom
'pdf-nowrap-fix' template (nbconvert-templates/pdf-nowrap-fix next to this
file) so long code lines and output
wrap instead of getting clipped off the page edge. Other formats (JSON,
Markdown, code, plain text/logs) are handled by converters.py, which renders
them to styled HTML and prints them through the same Chromium engine. One PDF
engine, many small adapters.

The UI is CustomTkinter (modern, themed, light/dark aware). CustomTkinter has
no table widget, so the queue list stays a ttk.Treeview -- it keeps row
selection for "Copy error" and is recolored to match the active theme. The
window root mixes CTk with tkinterdnd2 so drag-and-drop still works.

Files can be added two ways: drag-and-drop from Explorer, or the standard
Windows "Open" dialog (multi-select via ctrl/shift-click). Both are kept. On
a real drop the OS delivers each path brace-wrapped, so Tcl's splitlist parses
them correctly even when they contain spaces (e.g. "G:\\My Drive\\..."); the
handler additionally validates each result and falls back gracefully if a path
ever arrives un-braced.
"""

import queue
import subprocess
import sys
import tempfile
import threading
import time
import tkinter as tk
import traceback
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, ttk

import customtkinter as ctk
from tkinterdnd2 import DND_FILES, TkinterDnD

import converters

# nbconvert runs in a child process of the interpreter this GUI runs under. The
# launcher starts the GUI with pythonw.exe; the child uses its console sibling
# python.exe (its window is suppressed by CREATE_NO_WINDOW).
_CONSOLE_PYTHON = Path(sys.executable).with_name("python.exe")
PYTHON_EXE = str(_CONSOLE_PYTHON if _CONSOLE_PYTHON.exists() else Path(sys.executable))
TEMPLATE_BASE_DIR = str(Path(__file__).parent / "nbconvert-templates")
TEMPLATE_NAME = "pdf-nowrap-fix"
LOG_PATH = Path(__file__).parent / "conversion_log.txt"
STAGING_DIR = Path(__file__).parent / "_staging"
# Default output: a plain folder directly under the user profile, which is
# reliably writable. On this machine a whole set of locations are currently
# cloud-sync-managed and refusing new-file creation (the D: drive, the
# G:\My Drive mount, AND the Documents folder -- the last one even
# carries a ReadOnly attribute). The bare profile root and Desktop are NOT
# managed and write fine, so we default there and the user can override.
DEFAULT_OUTPUT_DIR = Path.home() / "Notebook PDFs"
FALLBACK_DIR = Path.home() / "Desktop" / "NotebookToPDF - could not save"


def convert_notebook(nb_path: Path, output_dir: Path) -> tuple[bool, str, str, Path | None]:
    """Returns (success, status_text, full_log_text, saved_path).

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
    log_chunks = [f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {nb_path}\n"]

    with tempfile.TemporaryDirectory(dir=STAGING_DIR) as tmp_dir_str:
        tmp_dir = Path(tmp_dir_str)
        cmd = [
            PYTHON_EXE, "-m", "jupyter", "nbconvert",
            "--to", "webpdf",
            "--template", TEMPLATE_NAME,
            f"--TemplateExporter.extra_template_basedirs={TEMPLATE_BASE_DIR}",
            "--output", nb_path.stem,
            "--output-dir", str(tmp_dir),
            str(nb_path),
        ]
        log_chunks.append(f"cmd: {cmd}\n")
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=600,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        except subprocess.TimeoutExpired:
            full_log = "".join(log_chunks) + "TIMED OUT after 10 minutes\n"
            _append_log(full_log)
            return False, "Failed: timed out after 10 minutes", full_log, None

        log_chunks.append(
            f"return code: {result.returncode}\n"
            f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}\n"
        )

        if result.returncode != 0:
            full_log = "".join(log_chunks)
            _append_log(full_log)
            lines = [l for l in result.stderr.strip().splitlines() if l.strip()]
            short = lines[-1] if lines else "Unknown error (see log)"
            return False, f"Failed: {short}", full_log, None

        produced = tmp_dir / f"{nb_path.stem}.pdf"
        if not produced.exists():
            full_log = "".join(log_chunks) + f"nbconvert reported success but {produced} is missing\n"
            _append_log(full_log)
            return False, "Failed: PDF missing after conversion (see log)", full_log, None

        return _save_pdf_bytes(produced.read_bytes(), nb_path.stem, output_dir, log_chunks)


def convert_file(src_path: Path, output_dir: Path) -> tuple[bool, str, str, Path | None]:
    """Convert a non-notebook file (JSON/Markdown/code/text) to PDF.

    Renders the file to styled HTML via converters.py, then prints it to PDF
    with the shared Chromium engine, and saves it with the same retry/fallback
    logic as notebooks.
    """
    log_chunks = [f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {src_path}\n"]
    try:
        pdf_bytes = converters.file_to_pdf_bytes(src_path)
    except Exception as e:  # noqa: BLE001 - surface any adapter/engine failure
        full_log = "".join(log_chunks) + "CONVERSION ERROR:\n" + traceback.format_exc()
        _append_log(full_log)
        return False, f"Failed: {e}", full_log, None
    # Keep the source extension in the PDF name (e.g. Compound1.json.pdf) so a
    # notebook and a same-named data file don't both collapse to Compound1.pdf.
    return _save_pdf_bytes(pdf_bytes, src_path.name, output_dir, log_chunks)


def convert_any(path: Path, output_dir: Path) -> tuple[bool, str, str, Path | None]:
    """Route a file to the right converter by extension."""
    if path.suffix.lower() == ".ipynb":
        return convert_notebook(path, output_dir)
    return convert_file(path, output_dir)


def _save_pdf_bytes(
    data: bytes, stem: str, output_dir: Path, log_chunks: list
) -> tuple[bool, str, str, Path | None]:
    """Write PDF bytes into output_dir, retrying, then falling back.

    Raw byte write (open 'wb') rather than shutil.copy2: copy2 also sets file
    metadata (os.utime), a second way to fail on the quirky volumes here --
    content-write can succeed while the metadata step raises. Bytes-only is the
    minimal, most-compatible operation. If output_dir keeps refusing the write,
    save to a guaranteed-writable fallback so a good PDF is never lost.
    """
    dest = output_dir / f"{stem}.pdf"
    last_error = None
    for attempt in range(4):
        try:
            if not output_dir.exists():
                output_dir.mkdir(parents=True, exist_ok=True)
            with open(dest, "wb") as f:
                f.write(data)
            last_error = None
            break
        except OSError as e:
            last_error = e
            log_chunks.append(f"copy attempt {attempt + 1} to {dest} failed: {e}\n")
            time.sleep(0.6 * (attempt + 1))

    if last_error is None:
        full_log = "".join(log_chunks) + f"Saved to {dest}\n"
        _append_log(full_log)
        return True, f"Done -> {dest}", full_log, dest

    FALLBACK_DIR.mkdir(parents=True, exist_ok=True)
    fallback_dest = FALLBACK_DIR / f"{stem}.pdf"
    with open(fallback_dest, "wb") as f:
        f.write(data)
    status = f"Saved to fallback (couldn't write {output_dir}): {fallback_dest}"
    log_chunks.append(status + "\n")
    full_log = "".join(log_chunks)
    _append_log(full_log)
    return True, status, full_log, fallback_dest


def _append_log(text: str) -> None:
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(text + "\n" + ("=" * 80) + "\n")
    except OSError:
        pass


ctk.set_appearance_mode("system")   # follows Windows light/dark
ctk.set_default_color_theme("blue")


class CTkDnD(ctk.CTk, TkinterDnD.DnDWrapper):
    """A CustomTkinter root that also speaks tkinterdnd2's drop protocol.

    CustomTkinter's CTk gives the themed window; DnDWrapper + _require load the
    tkdnd package into this interpreter so drop_target_register (already patched
    onto every tkinter widget by importing tkinterdnd2) actually works.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.TkdndVersion = TkinterDnD._require(self)


class App:
    def __init__(self, root: CTkDnD):
        self.root = root
        root.title("File -> PDF")
        root.geometry("820x560")
        root.minsize(680, 440)

        self.output_mode = tk.StringVar(value="local")
        self.custom_output_dir: Path | None = None
        self.auto_open = tk.BooleanVar(value=True)
        # Output dirs already surfaced in Explorer for the current batch. Persists
        # across _poll_results ticks so each folder opens at most once per batch
        # (a queue of N conversions no longer spawns N Explorer windows). Reset
        # in _enqueue_paths when a fresh batch is dropped in.
        self._opened_dirs: set[str] = set()
        self.row_by_path: dict[str, str] = {}
        self.full_message_by_row: dict[str, str] = {}
        self.work_queue: "queue.Queue[Path]" = queue.Queue()
        self.result_queue: "queue.Queue[tuple]" = queue.Queue()
        self._last_output_dir: Path | None = None

        self._build_ui()
        self._start_worker()
        self.root.after(100, self._poll_results)

    # ------------------------------------------------------------------ UI ---
    def _build_ui(self):
        root = self.root
        root.grid_columnconfigure(0, weight=1)
        root.grid_rowconfigure(3, weight=1)  # the queue list expands

        # --- header row: title + appearance switch ---------------------------
        header = ctk.CTkFrame(root, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=16, pady=(14, 4))
        header.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            header, text="File  →  PDF",
            font=ctk.CTkFont(size=22, weight="bold"),
        ).grid(row=0, column=0, sticky="w")
        ctk.CTkOptionMenu(
            header, width=110, values=["System", "Light", "Dark"],
            command=self._change_appearance,
        ).grid(row=0, column=1, sticky="e")

        # --- drop zone -------------------------------------------------------
        drop = ctk.CTkFrame(root, height=110, corner_radius=12, border_width=2)
        drop.grid(row=1, column=0, sticky="ew", padx=16, pady=8)
        drop.grid_propagate(False)
        drop.grid_columnconfigure(0, weight=1)
        drop.grid_rowconfigure(0, weight=1)
        self.drop_label = ctk.CTkLabel(
            drop,
            text="Drop files here, or click to select\n"
                 ".ipynb   .json   .md   .py / code   .txt / .log",
            font=ctk.CTkFont(size=14),
            justify="center",
        )
        self.drop_label.grid(row=0, column=0)
        for w in (drop, self.drop_label):
            w.configure(cursor="hand2")
            w.bind("<Button-1>", lambda e: self._browse_files())
            w.drop_target_register(DND_FILES)
            w.dnd_bind("<<Drop>>", self._on_drop)
        self._drop_frame = drop

        # --- options ---------------------------------------------------------
        opts = ctk.CTkFrame(root, corner_radius=12)
        opts.grid(row=2, column=0, sticky="ew", padx=16, pady=4)
        opts.grid_columnconfigure(3, weight=1)
        ctk.CTkRadioButton(
            opts, text=f"Save to  {DEFAULT_OUTPUT_DIR}   (recommended)",
            variable=self.output_mode, value="local",
            command=self._update_output_label,
        ).grid(row=0, column=0, columnspan=4, sticky="w", padx=12, pady=(12, 4))
        ctk.CTkRadioButton(
            opts, text="Save next to source", variable=self.output_mode,
            value="same", command=self._update_output_label,
        ).grid(row=1, column=0, sticky="w", padx=12, pady=4)
        ctk.CTkRadioButton(
            opts, text="Save to:", variable=self.output_mode,
            value="custom", command=self._choose_output_dir,
        ).grid(row=1, column=1, sticky="w", padx=(12, 4), pady=4)
        self.output_label = ctk.CTkLabel(opts, text="", text_color=("gray40", "gray70"))
        self.output_label.grid(row=1, column=2, columnspan=2, sticky="w", pady=4)
        ctk.CTkCheckBox(
            opts, text="Open folder when done", variable=self.auto_open,
        ).grid(row=2, column=0, columnspan=4, sticky="w", padx=12, pady=(4, 12))

        # --- queue list (ttk.Treeview, themed to match) ----------------------
        tree_wrap = ctk.CTkFrame(root, corner_radius=12)
        tree_wrap.grid(row=3, column=0, sticky="nsew", padx=16, pady=4)
        tree_wrap.grid_columnconfigure(0, weight=1)
        tree_wrap.grid_rowconfigure(0, weight=1)
        self.tree = ttk.Treeview(
            tree_wrap, columns=("file", "status"), show="headings",
            style="Conv.Treeview",
        )
        self.tree.heading("file", text="File")
        self.tree.heading("status", text="Status")
        self.tree.column("file", width=440)
        self.tree.column("status", width=300)
        self.tree.grid(row=0, column=0, sticky="nsew", padx=8, pady=8)
        sb = ctk.CTkScrollbar(tree_wrap, command=self.tree.yview)
        sb.grid(row=0, column=1, sticky="ns", pady=8, padx=(0, 8))
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.drop_target_register(DND_FILES)
        self.tree.dnd_bind("<<Drop>>", self._on_drop)
        self._style_tree()

        # --- bottom buttons --------------------------------------------------
        bottom = ctk.CTkFrame(root, fg_color="transparent")
        bottom.grid(row=4, column=0, sticky="ew", padx=16, pady=(4, 14))
        ctk.CTkButton(bottom, text="Select file(s)…", command=self._browse_files).pack(side="left")

        # Secondary (outline) buttons. They need an explicit text/border color:
        # the blue theme's default button text is pale (meant to sit on a blue
        # fill), so on a transparent fill over the light-mode background it was
        # nearly invisible. ("gray10","gray90") = dark text in light mode, light
        # text in dark mode.
        def secondary(text, cmd, width=110):
            return ctk.CTkButton(
                bottom, text=text, width=width, command=cmd,
                fg_color="transparent", border_width=1,
                text_color=("gray10", "gray90"),
                border_color=("gray60", "gray45"),
                hover_color=("gray85", "gray25"),
            )
        secondary("Clear list", self._clear_list, 90).pack(side="left", padx=6)
        secondary("Open output folder", self._open_last_output).pack(side="left")
        secondary("Copy error", self._copy_selected_error, 90).pack(side="left", padx=6)
        secondary("Open full log", self._open_log_file).pack(side="left")

        self._update_output_label()

    def _style_tree(self):
        """Recolor the ttk.Treeview to match the active CustomTkinter theme."""
        mode = ctk.get_appearance_mode()  # resolves "System" to Light/Dark
        if mode == "Dark":
            bg, fg, sel = "#2b2b2b", "#dce4ee", "#1f6aa5"
            head_bg, head_fg = "#333333", "#dce4ee"
        else:
            bg, fg, sel = "#fbfbfb", "#1a1a1a", "#3a7ebf"
            head_bg, head_fg = "#e6e6e6", "#1a1a1a"
        style = ttk.Style()
        style.theme_use("default")
        style.configure(
            "Conv.Treeview", background=bg, foreground=fg, fieldbackground=bg,
            borderwidth=0, rowheight=34, font=("Segoe UI", 12),
        )
        style.map(
            "Conv.Treeview",
            background=[("selected", sel)], foreground=[("selected", "#ffffff")],
        )
        style.configure(
            "Conv.Treeview.Heading", background=head_bg, foreground=head_fg,
            borderwidth=0, font=("Segoe UI", 12, "bold"),
        )
        style.map("Conv.Treeview.Heading", background=[("active", head_bg)])

    def _change_appearance(self, choice: str):
        ctk.set_appearance_mode(choice.lower())
        self._style_tree()

    # -------------------------------------------------------------- actions ---
    def _update_output_label(self):
        if self.output_mode.get() != "custom":
            self.output_label.configure(text="")

    def _choose_output_dir(self):
        chosen = filedialog.askdirectory(title="Choose output folder")
        if chosen:
            self.custom_output_dir = Path(chosen)
            self.output_label.configure(text=str(self.custom_output_dir))
        else:
            self.output_mode.set("same")
            self._update_output_label()

    def _browse_files(self):
        supported = " ".join(
            "*" + e for e in sorted({".ipynb", *converters.SUPPORTED_EXTENSIONS})
        )
        paths = filedialog.askopenfilenames(
            title="Choose files",
            filetypes=[
                ("Supported files", supported),
                ("Jupyter notebooks", "*.ipynb"),
                ("JSON", "*.json"),
                ("Markdown", "*.md *.markdown"),
                ("All files", "*.*"),
            ],
        )
        self._enqueue_paths(paths)

    def _on_drop(self, event):
        self._enqueue_paths(self._parse_drop(event.data))

    def _parse_drop(self, data: str) -> list[str]:
        """Parse the drop payload into a list of real file paths.

        Windows delivers each dropped path brace-wrapped, so splitlist parses
        space-containing paths correctly. We keep only entries that actually
        exist; if nothing survives (e.g. a rare un-braced payload), fall back
        to treating the whole trimmed string as a single path.
        """
        try:
            parts = list(self.root.tk.splitlist(data))
        except Exception:
            parts = []
        good = [p for p in parts if p and Path(p).exists()]
        if good:
            return good
        stripped = data.strip().strip("{}")
        return [stripped] if stripped and Path(stripped).exists() else []

    def _enqueue_paths(self, paths):
        # New batch: let each output folder pop open once more.
        self._opened_dirs.clear()
        handled = {".ipynb", *converters.SUPPORTED_EXTENSIONS}
        for raw in paths:
            path = Path(raw)
            # Unknown extensions still convert (routed to the text adapter), so
            # accept anything that's a file; only skip directories.
            if not path.is_file():
                continue
            note = "" if path.suffix.lower() in handled else " (as text)"
            row_id = self.tree.insert("", "end", values=(path.name, "Queued" + note))
            self.row_by_path[str(path)] = row_id
            self.work_queue.put(path)

    def _start_worker(self):
        threading.Thread(target=self._worker_loop, daemon=True).start()

    def _worker_loop(self):
        while True:
            path = self.work_queue.get()
            mode = self.output_mode.get()
            if mode == "custom" and self.custom_output_dir:
                output_dir = self.custom_output_dir
            elif mode == "same":
                output_dir = path.parent
            else:  # "local" -- the reliable default
                output_dir = DEFAULT_OUTPUT_DIR
            self.result_queue.put((path, None, "Converting…", "", None))
            ok, short_status, full_log, saved_path = convert_any(path, output_dir)
            self.result_queue.put((path, ok, short_status, full_log, saved_path))

    def _poll_results(self):
        try:
            while True:
                path, ok, short_status, full_log, saved_path = self.result_queue.get_nowait()
                row_id = self.row_by_path.get(str(path))
                if row_id is None:
                    continue
                self.tree.set(row_id, "status", short_status)
                if ok is not None:
                    self.full_message_by_row[row_id] = full_log
                if ok and saved_path is not None:
                    self._last_output_dir = saved_path.parent
                    if self.auto_open.get() and str(saved_path.parent) not in self._opened_dirs:
                        self._opened_dirs.add(str(saved_path.parent))
                        # select the file in Explorer so the user sees it directly
                        subprocess.run(["explorer", "/select,", str(saved_path)])
        except queue.Empty:
            pass
        self.root.after(150, self._poll_results)

    def _clear_list(self):
        for row_id in self.tree.get_children():
            self.tree.delete(row_id)
        self.row_by_path.clear()
        self.full_message_by_row.clear()

    def _open_last_output(self):
        target = self._last_output_dir or Path.home()
        subprocess.run(["explorer", str(target)])

    def _copy_selected_error(self):
        selection = self.tree.selection()
        if not selection:
            return
        message = self.full_message_by_row.get(
            selection[0], "(no log captured for this row yet)"
        )
        self.root.clipboard_clear()
        self.root.clipboard_append(message)

    def _open_log_file(self):
        if not LOG_PATH.exists():
            LOG_PATH.touch()
        subprocess.run(["notepad.exe", str(LOG_PATH)])


def main():
    root = CTkDnD()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
