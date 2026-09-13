"""
File -> PDF: pick files and get PDFs out.

Conversion itself lives in pipeline.py.

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
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, ttk

import customtkinter as ctk
from tkinterdnd2 import DND_FILES, TkinterDnD

import converters
import pipeline

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
        # The last non-custom output option, restored if the folder picker is
        # cancelled before any custom folder was chosen.
        self._last_plain_mode = "local"
        self.full_message_by_row: dict[str, str] = {}
        # Jobs are (row_id, path, output_dir); results are (row_id, Result),
        # with None as the Result meaning "started". Keying by Treeview row id
        # keeps a file dropped twice as two independent rows.
        self.work_queue: "queue.Queue[tuple[str, Path, Path]]" = queue.Queue()
        self.result_queue: "queue.Queue[tuple[str, pipeline.Result | None]]" = queue.Queue()
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
            opts, text=f"Save to  {pipeline.DEFAULT_OUTPUT_DIR}   (recommended)",
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
        mode = self.output_mode.get()
        if mode != "custom":
            self._last_plain_mode = mode
            self.output_label.configure(text="")

    def _choose_output_dir(self):
        chosen = filedialog.askdirectory(title="Choose output folder")
        if chosen:
            self.custom_output_dir = Path(chosen)
            self.output_label.configure(text=str(self.custom_output_dir))
        elif self.custom_output_dir is None:
            # Cancelled with no folder to fall back on: return to the option
            # that was selected, instead of switching to some other destination.
            self.output_mode.set(self._last_plain_mode)

    def _output_dir_for(self, path: Path) -> Path:
        mode = self.output_mode.get()
        if mode == "custom" and self.custom_output_dir:
            return self.custom_output_dir
        if mode == "same":
            return path.parent
        return pipeline.DEFAULT_OUTPUT_DIR  # "local", the reliable default

    def _browse_files(self):
        # Built from the converters' own routing (Pygments' lexer table), so it
        # never lags behind what actually converts. The dialog shows only the
        # label, not the several hundred patterns.
        supported = " ".join(["*.ipynb", *converters.dialog_patterns()])
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
        for raw in paths:
            path = Path(raw)
            # Unknown extensions still convert (routed to the text adapter), so
            # accept anything that's a file; only skip directories.
            if not path.is_file():
                continue
            recognized = path.suffix.lower() == ".ipynb" or converters.is_recognized(path)
            note = "" if recognized else " (as text)"
            row_id = self.tree.insert("", "end", values=(path.name, "Queued" + note))
            # The destination is fixed here, on the Tk thread: changing the
            # output option later only affects files added later, and the
            # worker never has to read Tk variables.
            self.work_queue.put((row_id, path, self._output_dir_for(path)))

    def _start_worker(self):
        threading.Thread(target=self._worker_loop, daemon=True).start()

    def _worker_loop(self):
        # Off the Tk thread: reads work_queue, writes result_queue, nothing else.
        # pipeline.convert_any never raises, so this loop outlives any bad file.
        while True:
            row_id, path, output_dir = self.work_queue.get()
            self.result_queue.put((row_id, None))
            self.result_queue.put((row_id, pipeline.convert_any(path, output_dir)))

    def _poll_results(self):
        try:
            while True:
                row_id, result = self.result_queue.get_nowait()
                if not self.tree.exists(row_id):  # removed by "Clear list"
                    continue
                if result is None:
                    self.tree.set(row_id, "status", "Converting…")
                    continue
                self.tree.set(row_id, "status", result.status)
                self.full_message_by_row[row_id] = result.log
                if result.ok and result.saved_path is not None:
                    folder = result.saved_path.parent
                    self._last_output_dir = folder
                    if self.auto_open.get() and str(folder) not in self._opened_dirs:
                        self._opened_dirs.add(str(folder))
                        # select the file in Explorer so the user sees it directly
                        subprocess.Popen(["explorer", "/select,", str(result.saved_path)])
        except queue.Empty:
            pass
        self.root.after(150, self._poll_results)

    def _clear_list(self):
        for row_id in self.tree.get_children():
            self.tree.delete(row_id)
        self.full_message_by_row.clear()

    def _open_last_output(self):
        target = self._last_output_dir or Path.home()
        subprocess.Popen(["explorer", str(target)])

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
        if not pipeline.LOG_PATH.exists():
            pipeline.LOG_PATH.touch()
        # Popen, not run: waiting for the editor to exit would freeze the window.
        subprocess.Popen(["notepad.exe", str(pipeline.LOG_PATH)])


def main():
    root = CTkDnD()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
