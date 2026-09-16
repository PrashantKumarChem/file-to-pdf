"""
File to PDF window: pick files and get PDFs out.

Conversion itself lives in pipeline.py.

The UI is CustomTkinter (modern, themed, light/dark aware). CustomTkinter has
no table widget, so the queue list stays a ttk.Treeview -- it keeps row
selection for "Copy details" and is restyled to match the active theme and
the display scaling. The window root mixes CTk with tkinterdnd2 so
drag-and-drop still works.

Files can be added three ways: drag-and-drop from Explorer (files or
folders), the standard Windows "Open" dialog (multi-select via
ctrl/shift-click), or Ctrl+O. On a real drop the OS delivers each path
brace-wrapped, so Tcl's splitlist parses them correctly even when they contain
spaces; the handler additionally validates each result and falls back
gracefully if a path ever arrives un-braced.
"""

import contextlib
import queue
import subprocess
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, ttk
from typing import Any

import customtkinter as ctk
from tkinterdnd2 import COPY, DND_FILES, TkinterDnD

from topdf import APP_NAME, __version__, cli, converters, pipeline

ctk.set_appearance_mode("system")  # follows Windows light/dark
ctk.set_default_color_theme("blue")

# Row states, used as Treeview tags to color the Status column.
PENDING, DONE, WARNING, FAILED = "pending", "done", "warning", "failed"
# (light, dark) text color per state; None keeps the list's own text color, so
# a finished row reads normally and only what needs attention stands out.
STATE_COLORS: dict[str, tuple[str, str] | None] = {
    PENDING: ("#6b6b6b", "#9a9a9a"),
    DONE: None,
    WARNING: ("#8a5a00", "#e3b341"),
    FAILED: ("#c42b1c", "#ff8f8f"),
}
# Marks in front of the status, so the state doesn't rest on color alone.
STATE_MARKS = {DONE: "✓ ", WARNING: "⚠ ", FAILED: "✕ "}
ACCENT = ("#3B8ED0", "#1F6AA5")
# How long a short message ("Copied ...") replaces the batch summary.
FLASH_MS = 4000

# The drop area lists what converts, one line per group of (kind, extensions).
# Every extension here must be one converters.py recognizes (a test checks);
# code is a sample of the several hundred Pygments knows.
FILE_TYPE_LINES = [
    [
        ("Notebooks", [".ipynb"]),
        ("JSON", [".json"]),
        ("Markdown", [".md", ".markdown"]),
        ("Text, as written", [".txt", ".log", ".csv", ".dat", ".out"]),
    ],
    [
        (
            "Code, as highlighted source",
            [
                ".py",
                ".R",
                ".jl",
                ".m",
                ".js",
                ".ts",
                ".c",
                ".cpp",
                ".java",
                ".sh",
                ".sql",
                ".tex",
                ".yaml",
                "and hundreds more",
            ],
        ),
    ],
]
OTHER_FILES_NOTE = "Any other file that reads as text (.xyz, .inp, .gjf …) prints as plain text."

# Shown in the empty list: what the window can do, before anything is added.
EMPTY_LIST_TEXT = """\
Nothing here yet. What File to PDF does:

•  Drop files, or whole folders, anywhere in this window, or press Ctrl+O
•  Notebooks keep long code and wide output on the page
•  JSON, Markdown, code and text print exactly as written, with nothing added
•  Double-click a converted file to open its PDF; right-click for more

Press F1 or Help for everything else."""

# The Help window: (heading, text). Keep it to what the window and the
# command line really do; README.md has the details.
HELP_SECTIONS = [
    (
        "What it converts",
        "Jupyter notebooks (.ipynb) are exported the way nbconvert prints them, with long code lines, output "
        "tables and wide figures wrapped or scaled to fit the page.\n"
        "JSON, Markdown, code and plain text print 1:1: JSON and code highlighted exactly as written, Markdown as "
        "the rendered document. Code prints as its source, so .html and .tex give the markup rather than the "
        "rendered page, and .csv gives its lines as written rather than a table. The PDF holds the file's "
        "content and nothing else: no heading, no file name.\n"
        "Files of any other type print as text if they read as text (.xyz, .log, input decks). Binary files, such "
        "as images, fail with a message instead of printing garbage.",
    ),
    (
        "Adding files",
        "Drop files or folders anywhere in the window, click the drop area, press Ctrl+O, or use Add files. "
        "A dropped folder adds the files in it that print, leaving out subfolders, PDFs, images and hidden files. "
        "Files convert one after another while you keep adding more.",
    ),
    (
        "Where PDFs go",
        f"In the File to PDF folder ({pipeline.DEFAULT_OUTPUT_DIR}), next to each file, or in a folder you choose. "
        "The choice applies to files added after you change it.\n"
        "analysis.ipynb becomes analysis.pdf; other files keep their extension, so data.json becomes "
        "data.json.pdf. Converting a file again replaces its PDF. If a folder refuses the PDF, it is saved to "
        f"{pipeline.FALLBACK_DIR} instead.",
    ),
    (
        "In the list",
        "Double-click a converted file to open its PDF. Right-click a row to open the PDF, show it in its "
        "folder, copy the details of what happened, or remove the row. Removing a file that hasn't started yet "
        "skips it. Open log shows every conversion in full.",
    ),
    (
        "Keyboard",
        "Ctrl+O: add files\n"
        "Delete: remove the selected rows\n"
        "Ctrl+C: copy the details of the selected row\n"
        "Ctrl+A: select every row\n"
        "F1: this help",
    ),
    (
        "Notebooks and the web",
        "Printing a notebook loads MathJax and chart libraries from the web. Offline, the PDF is still saved, "
        "the status shows a warning, and math or charts may be missing.",
    ),
    (
        "Command line",
        "topdf converts files, folders (with --recursive, their subfolders too) and wildcards the same way, for "
        "scripts and large batches. It is topdf.exe in the app's folder; from source, run "
        "uv run python -m topdf. Run it with --help to see the options.",
    ),
]


class Tooltip:
    """A short explanation that appears when the pointer rests on a widget."""

    DELAY_MS = 600

    def __init__(self, widget: Any, text: str):
        self.widget = widget
        self.text = text
        self._pending: str | None = None
        self._tip: tk.Toplevel | None = None
        widget.bind("<Enter>", self._schedule, add=True)
        widget.bind("<Leave>", self._hide, add=True)
        widget.bind("<ButtonPress>", self._hide, add=True)

    def _schedule(self, _event=None):
        self._cancel()
        self._pending = self.widget.after(self.DELAY_MS, self.show)

    def _cancel(self):
        if self._pending is not None:
            self.widget.after_cancel(self._pending)
            self._pending = None

    def show(self):
        self._pending = None
        if self._tip is not None or not self.widget.winfo_exists():
            return
        scale = ctk.ScalingTracker.get_widget_scaling(self.widget)
        dark = ctk.get_appearance_mode() == "Dark"
        self._tip = tip = tk.Toplevel(self.widget)
        tip.wm_overrideredirect(True)
        tip.attributes("-topmost", True)
        tk.Label(
            tip,
            text=self.text,
            justify="left",
            wraplength=round(320 * scale),
            background="#3a3a3a" if dark else "#fffdf2",
            foreground="#f0f0f0" if dark else "#1a1a1a",
            borderwidth=1,
            relief="solid",
            padx=round(8 * scale),
            pady=round(5 * scale),
            font=("Segoe UI", -round(12 * scale)),
        ).pack()
        x = self.widget.winfo_rootx() + round(10 * scale)
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + round(4 * scale)
        tip.wm_geometry(f"+{x}+{y}")

    def _hide(self, _event=None):
        self._cancel()
        if self._tip is not None:
            self._tip.destroy()
            self._tip = None


class CTkDnD(ctk.CTk, TkinterDnD.DnDWrapper):
    """A CustomTkinter root that also speaks tkinterdnd2's drop protocol.

    CustomTkinter's CTk gives the themed window; DnDWrapper + _require load the
    tkdnd package into this interpreter so drop_target_register (already patched
    onto every tkinter widget by importing tkinterdnd2) actually works.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.TkdndVersion = TkinterDnD._require(self)


def accept_file_drops(widget: Any, on_drop, on_enter, on_leave) -> None:
    """Make widget a drop target for files from Explorer.

    Importing tkinterdnd2 adds drop_target_register and dnd_bind to every
    tkinter widget at run time, which type checkers can't see, hence Any.
    """
    widget.drop_target_register(DND_FILES)
    widget.dnd_bind("<<Drop>>", on_drop)
    widget.dnd_bind("<<DropEnter>>", on_enter)
    widget.dnd_bind("<<DropLeave>>", on_leave)


def short_status(result: pipeline.Result) -> str:
    """The status without the saved path, which has its own column."""
    if result.saved_path is not None:
        for separator in (" -> ", ": "):
            result_suffix = f"{separator}{result.saved_path}"
            if result.status.endswith(result_suffix):
                return result.status.removesuffix(result_suffix)
    return result.status


def state_of(result: pipeline.Result) -> str:
    if not result.ok:
        return FAILED
    # A warning (web resources that did not load) or a PDF that went to the
    # fallback folder: saved, but not quite what was asked for.
    if result.status.startswith(("Warning", "Saved to fallback")):
        return WARNING
    return DONE


def elide(text: str, limit: int) -> str:
    """Shorten text in the middle, so both ends of a long path stay readable."""
    if len(text) <= limit:
        return text
    keep = limit - 1
    return text[: keep // 2] + "…" + text[-(keep - keep // 2) :]


class App:
    def __init__(self, root: CTkDnD):
        self.root = root
        root.title(APP_NAME)
        root.geometry("860x600")
        root.minsize(700, 540)

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
        self.saved_path_by_row: dict[str, Path] = {}
        self.state_by_row: dict[str, str] = {}
        # Unfinished rows removed from the list (Clear list, Delete). The worker
        # skips a removed row's job that hasn't started yet and forgets each id
        # once its job is handled; set operations are safe across the threads.
        self._removed_rows: set[str] = set()
        # Jobs are (row_id, path, output_dir); results are (row_id, Result),
        # with None as the Result meaning "started". Keying by Treeview row id
        # keeps a file dropped twice as two independent rows.
        self.work_queue: queue.Queue[tuple[str, Path, Path]] = queue.Queue()
        self.result_queue: queue.Queue[tuple[str, pipeline.Result | None]] = queue.Queue()
        self._last_output_dir: Path | None = None
        self._flash_id: str | None = None
        self._summary_text = ""
        self.help_window: ctk.CTkToplevel | None = None

        self._build_ui()
        self._start_worker()
        self.root.after(100, self._poll_results)

    # ------------------------------------------------------------------ UI ---
    def _build_ui(self):
        root = self.root
        root.grid_columnconfigure(0, weight=1)
        root.grid_rowconfigure(3, weight=1)  # the queue list expands
        muted = ("gray40", "gray65")

        # --- header row: name, version, appearance switch --------------------
        header = ctk.CTkFrame(root, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=16, pady=(12, 2))
        header.grid_columnconfigure(2, weight=1)
        ctk.CTkLabel(header, text=APP_NAME, font=ctk.CTkFont(size=20, weight="bold")).grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(header, text=f"v{__version__}", text_color=muted).grid(
            row=0, column=1, sticky="sw", padx=(8, 0), pady=(0, 3)
        )
        ctk.CTkLabel(header, text="Theme", text_color=muted).grid(row=0, column=3, sticky="e", padx=(0, 8))
        # Neutral colors: a rarely used setting shouldn't be the loudest control.
        theme = ctk.CTkOptionMenu(
            header,
            width=96,
            values=["System", "Light", "Dark"],
            command=self._change_appearance,
            fg_color=("gray84", "gray25"),
            button_color=("gray76", "gray30"),
            button_hover_color=("gray70", "gray35"),
            text_color=("gray10", "gray90"),
        )
        theme.grid(row=0, column=4, sticky="e")
        help_button = ctk.CTkButton(header, text="Help", width=60, command=self._show_help, **self._secondary_colors())
        help_button.grid(row=0, column=5, sticky="e", padx=(8, 0))
        Tooltip(theme, "Follow Windows, or always use the light or dark look.")
        Tooltip(help_button, "What File to PDF can do, and its keyboard shortcuts (F1).")

        # --- drop zone -------------------------------------------------------
        self.drop_zone = drop = ctk.CTkFrame(root, height=168, corner_radius=12, border_width=2)
        drop.grid(row=1, column=0, sticky="ew", padx=16, pady=8)
        drop.grid_propagate(False)
        drop.grid_columnconfigure(0, weight=1)
        drop.grid_rowconfigure((0, 5), weight=1)
        self._drop_colors = {"fg_color": drop.cget("fg_color"), "border_color": drop.cget("border_color")}
        self.drop_label = ctk.CTkLabel(
            drop, text="Drop files or folders here", font=ctk.CTkFont(size=16, weight="bold")
        )
        self.drop_label.grid(row=1, column=0)
        self.drop_hint = ctk.CTkLabel(drop, text="or click to choose files", text_color=muted)
        self.drop_hint.grid(row=2, column=0, pady=(0, 4))
        # The file types, one line per group: a bold kind, then its extensions.
        self.drop_widgets: list[Any] = [drop, self.drop_label, self.drop_hint]
        for row, line in enumerate(FILE_TYPE_LINES, start=3):
            line_frame = ctk.CTkFrame(drop, fg_color="transparent")
            line_frame.grid(row=row, column=0)
            self.drop_widgets.append(line_frame)
            for kind, extensions in line:
                kind_label = ctk.CTkLabel(line_frame, text=kind, font=ctk.CTkFont(size=12, weight="bold"), height=20)
                kind_label.pack(side="left", padx=(12, 5))
                ext_label = ctk.CTkLabel(
                    line_frame, text="  ".join(extensions), font=ctk.CTkFont(size=12), text_color=muted, height=20
                )
                ext_label.pack(side="left")
                self.drop_widgets += [kind_label, ext_label]
        self.drop_note = ctk.CTkLabel(
            drop, text=OTHER_FILES_NOTE, font=ctk.CTkFont(size=12), text_color=muted, height=20
        )
        self.drop_note.grid(row=5, column=0, sticky="n", pady=(2, 0))
        self.drop_widgets.append(self.drop_note)
        for w in self.drop_widgets:
            w.configure(cursor="hand2")
            w.bind("<Button-1>", lambda e: self._browse_files())
            Tooltip(
                w,
                "Add as many files as you like; they convert one after another. A folder adds the files "
                "in it that print, but not its subfolders, PDFs or images.",
            )

        # --- output options --------------------------------------------------
        opts = ctk.CTkFrame(root, corner_radius=12)
        opts.grid(row=2, column=0, sticky="ew", padx=16, pady=4)
        opts.grid_columnconfigure(5, weight=1)
        ctk.CTkLabel(opts, text="Save PDFs", font=ctk.CTkFont(weight="bold")).grid(
            row=0, column=0, sticky="w", padx=(14, 10), pady=(10, 2)
        )
        options = [
            ("In the File to PDF folder", "local", f"Every PDF goes to {pipeline.DEFAULT_OUTPUT_DIR}."),
            ("Next to each file", "same", "Each PDF is saved in the folder of the file it came from."),
            ("In another folder", "custom", "Every PDF goes to a folder you choose."),
        ]
        for column, (text, value, tip) in enumerate(options, start=1):
            radio = ctk.CTkRadioButton(
                opts,
                text=text,
                variable=self.output_mode,
                value=value,
                command=self._on_output_mode,
                radiobutton_width=18,
                radiobutton_height=18,
            )
            radio.grid(row=0, column=column, sticky="w", padx=(0, 14), pady=(10, 2))
            Tooltip(radio, tip + " The choice applies to files added after you change it.")
        # The destination and its Change button share a frame, so the button
        # follows the path however long it is.
        where = ctk.CTkFrame(opts, fg_color="transparent")
        where.grid(row=1, column=1, columnspan=2, sticky="w", pady=(0, 10))
        self.output_label = ctk.CTkLabel(where, text="", text_color=muted, anchor="w")
        self.output_label.pack(side="left")
        self.change_button = ctk.CTkButton(
            where,
            text="Change…",
            width=80,
            height=24,
            command=self._choose_output_dir,
            **self._secondary_colors(),
        )
        auto_open = ctk.CTkCheckBox(
            opts,
            text="Open folder when done",
            variable=self.auto_open,
            checkbox_width=18,
            checkbox_height=18,
        )
        auto_open.grid(row=1, column=3, columnspan=3, sticky="e", padx=(8, 14), pady=(0, 10))
        Tooltip(auto_open, "Shows each new PDF selected in Explorer, one window per folder per batch.")

        # --- queue list (ttk.Treeview, themed to match) ----------------------
        self.tree_wrap = tree_wrap = ctk.CTkFrame(root, corner_radius=12)
        tree_wrap.grid(row=3, column=0, sticky="nsew", padx=16, pady=4)
        tree_wrap.grid_columnconfigure(0, weight=1)
        tree_wrap.grid_rowconfigure(0, weight=1)
        self.tree = ttk.Treeview(
            tree_wrap,
            columns=("file", "status", "saved"),
            show="headings",
            style="Conv.Treeview",
        )
        self.tree.heading("file", text="File", anchor="w")
        self.tree.heading("status", text="Status", anchor="w")
        self.tree.heading("saved", text="Saved as", anchor="w")
        self.tree.grid(row=0, column=0, sticky="nsew", padx=(10, 0), pady=10)
        sb = ctk.CTkScrollbar(tree_wrap, command=self.tree.yview)
        sb.grid(row=0, column=1, sticky="ns", pady=10, padx=(2, 6))
        self.tree.configure(yscrollcommand=sb.set)
        self.empty_label = ctk.CTkLabel(tree_wrap, text=EMPTY_LIST_TEXT, text_color=muted, justify="left")
        self.tree.bind("<Double-1>", self._on_double_click)
        self.tree.bind("<Button-3>", self._on_right_click)
        self.tree.bind("<Delete>", lambda e: self._remove_selected())
        self.tree.bind("<Control-c>", lambda e: self._copy_selected_error())
        self.tree.bind("<Control-a>", lambda e: self.tree.selection_set(self.tree.get_children()))
        self.menu = tk.Menu(root, tearoff=False)
        self.menu.add_command(label="Open PDF", command=self._open_selected_pdf)
        self.menu.add_command(label="Show in folder", command=self._show_selected_in_folder)
        self.menu.add_command(label="Copy details", command=self._copy_selected_error)
        self.menu.add_separator()
        self.menu.add_command(label="Remove from list", command=self._remove_selected)

        # --- summary line ----------------------------------------------------
        status_bar = ctk.CTkFrame(root, fg_color="transparent")
        status_bar.grid(row=4, column=0, sticky="ew", padx=20, pady=(2, 0))
        status_bar.grid_columnconfigure(0, weight=1)
        self.summary_label = ctk.CTkLabel(status_bar, text="", text_color=muted, anchor="w")
        self.summary_label.grid(row=0, column=0, sticky="w")
        self.progress = ctk.CTkProgressBar(status_bar, width=180, height=8)
        self.progress.grid(row=0, column=1, sticky="e")
        self.progress.grid_remove()

        # --- bottom buttons --------------------------------------------------
        bottom = ctk.CTkFrame(root, fg_color="transparent")
        bottom.grid(row=5, column=0, sticky="ew", padx=16, pady=(4, 14))
        add = ctk.CTkButton(bottom, text="Add files…", width=120, command=self._browse_files)
        add.pack(side="left")
        Tooltip(add, "Choose files to convert (Ctrl+O). To add a whole folder, drop it on the window.")

        def secondary(text, cmd, width, tip, **pack):
            button = ctk.CTkButton(bottom, text=text, width=width, command=cmd, **self._secondary_colors())
            button.pack(**pack)
            Tooltip(button, tip)

        secondary(
            "Clear list",
            self._clear_list,
            90,
            "Empty the list. Files that haven't started are skipped; PDFs already saved stay.",
            side="left",
            padx=6,
        )
        secondary(
            "Open log", self._open_log_file, 90, "Open the log, which records every conversion in full.", side="right"
        )
        secondary(
            "Copy details",
            self._copy_selected_error,
            110,
            "Copy what happened to the selected file, including any error (Ctrl+C).",
            side="right",
            padx=6,
        )
        secondary(
            "Open output folder",
            self._open_last_output,
            140,
            "Open the folder the last PDF was saved in.",
            side="right",
        )

        # Drops land anywhere in the window, not only on the drop zone.
        for w in (root, *self.drop_widgets, self.tree, self.empty_label):
            accept_file_drops(w, self._on_drop, self._on_drag_enter, self._on_drag_leave)
        root.bind("<Control-o>", lambda e: self._browse_files())
        root.bind("<Control-O>", lambda e: self._browse_files())
        root.bind("<F1>", lambda e: self._show_help())
        # Follow Windows switching between light and dark while "System" is set.
        ctk.AppearanceModeTracker.add(self._on_appearance_changed, root)
        root.bind("<Destroy>", self._on_destroy, add="+")

        self._style_tree()
        self._on_output_mode()
        self._refresh_summary()

    @staticmethod
    def _secondary_colors() -> dict:
        # Outline buttons need an explicit text/border color: the blue theme's
        # default button text is pale (meant to sit on a blue fill), so on a
        # transparent fill over the light-mode background it was nearly
        # invisible.
        return {
            "fg_color": "transparent",
            "border_width": 1,
            "text_color": ("gray10", "gray90"),
            "border_color": ("gray60", "gray45"),
            "hover_color": ("gray85", "gray25"),
        }

    def _style_tree(self):
        """Restyle the ttk.Treeview for the active theme and display scaling.

        ttk sizes aren't scaled by CustomTkinter, so fonts, row height and
        column widths are multiplied by its scaling factor here; otherwise the
        list's text is half the size of everything around it at 200 %.
        """
        dark = ctk.get_appearance_mode() == "Dark"  # resolves "System" to Light/Dark
        scale = ctk.ScalingTracker.get_widget_scaling(self.root)
        if dark:
            bg, fg, sel = "#212121", "#dce4ee", "#1f6aa5"
            head_bg, head_fg = "#2e2e2e", "#dce4ee"
        else:
            bg, fg, sel = "#fbfbfb", "#1a1a1a", "#cfe3f7"
            head_bg, head_fg = "#ececec", "#1a1a1a"
        style = ttk.Style(self.root)
        style.theme_use("default")
        style.configure(
            "Conv.Treeview",
            background=bg,
            foreground=fg,
            fieldbackground=bg,
            borderwidth=0,
            rowheight=round(28 * scale),
            font=("Segoe UI", -round(13 * scale)),
        )
        style.map(
            "Conv.Treeview",
            background=[("selected", sel)],
            foreground=[("selected", "#ffffff" if dark else "#1a1a1a")],
        )
        style.configure(
            "Conv.Treeview.Heading",
            background=head_bg,
            foreground=head_fg,
            borderwidth=0,
            padding=(round(4 * scale), round(3 * scale)),
            font=("Segoe UI", -round(13 * scale), "bold"),
        )
        style.map("Conv.Treeview.Heading", background=[("active", head_bg)])
        style.layout("Conv.Treeview", [("Treeview.treearea", {"sticky": "nswe"})])  # no focus/border ring
        for column, width, stretch in (("file", 190, False), ("status", 250, True), ("saved", 250, True)):
            self.tree.column(column, width=round(width * scale), minwidth=round(80 * scale), stretch=stretch)
        for state, colors in STATE_COLORS.items():
            self.tree.tag_configure(state, foreground=colors[dark] if colors else fg)
        self.tree_wrap.configure(fg_color=(bg, bg))
        self.empty_label.configure(fg_color=bg)

    def _change_appearance(self, choice: str):
        ctk.set_appearance_mode(choice.lower())
        self._style_tree()

    def _on_appearance_changed(self, _mode: str):
        with contextlib.suppress(tk.TclError):  # the window is being destroyed
            self._style_tree()

    def _on_destroy(self, event):
        if event.widget is self.root:
            ctk.AppearanceModeTracker.remove(self._on_appearance_changed)

    def _on_drag_enter(self, event):
        self.drop_zone.configure(fg_color=("#dbe9f7", "#1d3246"), border_color=ACCENT)
        self.drop_label.configure(text="Release to add")
        return event.action or COPY

    def _on_drag_leave(self, _event=None):
        self.drop_zone.configure(**self._drop_colors)
        self.drop_label.configure(text="Drop files or folders here")

    # -------------------------------------------------------------- actions ---
    def _on_output_mode(self):
        mode = self.output_mode.get()
        if mode == "custom" and self.custom_output_dir is None:
            self._choose_output_dir()
            return
        if mode != "custom":
            self._last_plain_mode = mode
        self._update_output_label()

    def _update_output_label(self):
        mode = self.output_mode.get()
        if mode == "custom" and self.custom_output_dir is not None:
            text = elide(str(self.custom_output_dir), 44)
            self.change_button.pack(side="left", padx=(8, 0))
        else:
            text = "The folder each file is in" if mode == "same" else elide(str(pipeline.DEFAULT_OUTPUT_DIR), 44)
            self.change_button.pack_forget()
        self.output_label.configure(text=text)

    def _choose_output_dir(self):
        chosen = filedialog.askdirectory(title="Choose output folder")
        if chosen:
            self.custom_output_dir = Path(chosen)
            self.output_mode.set("custom")
        elif self.custom_output_dir is None:
            # Cancelled with no folder to fall back on: return to the option
            # that was selected, instead of switching to some other destination.
            self.output_mode.set(self._last_plain_mode)
        self._update_output_label()

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
        supported = " ".join(converters.dialog_patterns())
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
        self._on_drag_leave()
        self._enqueue_paths(self._parse_drop(event.data))
        return event.action or COPY

    def _parse_drop(self, data: str) -> list[str]:
        """Parse the drop payload into a list of real file paths.

        Windows delivers each dropped path brace-wrapped, so splitlist parses
        space-containing paths correctly. We keep only entries that actually
        exist; if nothing survives (e.g. a rare un-braced payload), fall back
        to treating the whole trimmed string as a single path.
        """
        try:
            parts = list(self.root.tk.splitlist(data))
        except Exception:  # noqa: BLE001 - an unparseable payload gets the single-path fallback below
            parts = []
        good = [p for p in parts if p and Path(p).exists()]
        if good:
            return good
        stripped = data.strip().strip("{}")
        return [stripped] if stripped and Path(stripped).exists() else []

    def _enqueue_paths(self, paths):
        # A file added directly always converts (unknown extensions as text),
        # even when it was added twice. A folder adds the files in it that
        # print, as the command line does without --recursive: no subfolders,
        # hidden files or PDFs.
        files: list[Path] = []
        skipped: list[Path] = []
        folders = 0
        for raw in paths:
            path = Path(raw)
            if path.is_file():
                files.append(path)
            elif path.is_dir():
                folders += 1
                found, _missing, left_out = cli.collect([str(path)])
                files += found
                skipped += left_out
        if not files and not folders:
            return
        # New batch: let each output folder pop open once more.
        self._opened_dirs.clear()
        for path in files:
            note = "" if converters.is_recognized(path) else " (as text)"
            row_id = self.tree.insert("", "end", values=(path.name, "Queued" + note, ""), tags=(PENDING,))
            self.state_by_row[row_id] = PENDING
            # The destination is fixed here, on the Tk thread: changing the
            # output option later only affects files added later, and the
            # worker never has to read Tk variables.
            self.work_queue.put((row_id, path, self._output_dir_for(path)))
        self._refresh_summary()
        if not files:
            self._flash("Nothing to convert: no files in that folder print (subfolders aren't included).")
        elif skipped:
            noun = "file" if len(skipped) == 1 else "files"
            self._flash(f"Left out {len(skipped)} {noun} that won't print (PDFs, images and other binary files).")

    def _start_worker(self):
        threading.Thread(target=self._worker_loop, daemon=True).start()

    def _worker_loop(self):
        # Off the Tk thread: reads work_queue, writes result_queue, nothing else.
        # pipeline.convert_any never raises, so this loop outlives any bad file.
        # Files waiting in the queue share one browser (converters.batch),
        # which closes as soon as the queue runs dry.
        while True:
            job = self.work_queue.get()
            with converters.batch():
                while job is not None:
                    row_id, path, output_dir = job
                    if row_id not in self._removed_rows:
                        self.result_queue.put((row_id, None))
                        self.result_queue.put((row_id, pipeline.convert_any(path, output_dir)))
                    self._removed_rows.discard(row_id)
                    try:
                        job = self.work_queue.get_nowait()
                    except queue.Empty:
                        job = None

    def _poll_results(self):
        changed = False
        try:
            while True:
                row_id, result = self.result_queue.get_nowait()
                if not self.tree.exists(row_id):  # removed from the list
                    continue
                changed = True
                if result is None:
                    self.tree.set(row_id, "status", "Converting…")
                    continue
                state = state_of(result)
                self.state_by_row[row_id] = state
                self.tree.item(row_id, tags=(state,))
                self.tree.set(row_id, "status", STATE_MARKS[state] + short_status(result))
                self.full_message_by_row[row_id] = result.log
                if result.saved_path is not None:
                    self.saved_path_by_row[row_id] = result.saved_path
                    self.tree.set(row_id, "saved", str(result.saved_path))
                if result.ok and result.saved_path is not None:
                    folder = result.saved_path.parent
                    self._last_output_dir = folder
                    if self.auto_open.get() and str(folder) not in self._opened_dirs:
                        self._opened_dirs.add(str(folder))
                        # select the file in Explorer so the user sees it directly
                        self._launch(["explorer", "/select,", str(result.saved_path)])
        except queue.Empty:
            pass
        if changed:
            self._refresh_summary()
        self.root.after(150, self._poll_results)

    def _refresh_summary(self):
        """Show or hide the empty-list hint, and summarize the rows below the list."""
        rows = self.tree.get_children()
        if rows:
            self.empty_label.place_forget()
        else:
            self.empty_label.place(relx=0.5, rely=0.5, anchor="center")
        states = [self.state_by_row.get(r, PENDING) for r in rows]
        pending, failed = states.count(PENDING), states.count(FAILED)
        finished = len(states) - pending
        parts = []
        if pending:
            parts.append(f"Converting… {finished} of {len(states)} finished")
            self.progress.grid()
            self.progress.set(finished / len(states))
        else:
            self.progress.grid_remove()
            if rows:
                parts.append(f"{finished - failed} converted")
        if failed:
            parts.append(f"{failed} failed")
        text = ", ".join(parts)
        # A hint for what to do next, once there is something to act on.
        if failed and not pending:
            text += "  ·  right-click a failed file and Copy details to see why"
        elif rows and not pending:
            text += "  ·  double-click a file to open its PDF"
        self._summary_text = text
        if self._flash_id is None:
            self.summary_label.configure(text=self._summary_text)

    def _flash(self, message: str):
        """Show a short message in place of the summary for a few seconds."""
        if self._flash_id is not None:
            self.root.after_cancel(self._flash_id)
        self.summary_label.configure(text=message)

        def restore():
            self._flash_id = None
            self.summary_label.configure(text=self._summary_text)

        self._flash_id = self.root.after(FLASH_MS, restore)

    def _remove_rows(self, row_ids):
        for row_id in row_ids:
            if self.state_by_row.get(row_id) == PENDING:
                self._removed_rows.add(row_id)
            self.tree.delete(row_id)
            self.full_message_by_row.pop(row_id, None)
            self.saved_path_by_row.pop(row_id, None)
            self.state_by_row.pop(row_id, None)
        self._refresh_summary()

    def _clear_list(self):
        # Files still waiting are dropped too; the one converting finishes.
        self._remove_rows(self.tree.get_children())

    def _remove_selected(self):
        self._remove_rows(self.tree.selection())

    def _on_double_click(self, event):
        row_id = self.tree.identify_row(event.y)
        if row_id:
            self.tree.selection_set(row_id)
            self._open_selected_pdf()

    def _on_right_click(self, event):
        row_id = self.tree.identify_row(event.y)
        if not row_id:
            return
        if row_id not in self.tree.selection():
            self.tree.selection_set(row_id)
        saved = "normal" if row_id in self.saved_path_by_row else "disabled"
        self.menu.entryconfigure("Open PDF", state=saved)
        self.menu.entryconfigure("Show in folder", state=saved)
        self.menu.tk_popup(event.x_root, event.y_root)

    def _selected_saved_path(self) -> Path | None:
        selection = self.tree.selection()
        return self.saved_path_by_row.get(selection[0]) if selection else None

    def _open_selected_pdf(self):
        path = self._selected_saved_path()
        if path is None:
            self._flash("Select a converted file first.")
        elif path.exists():
            self._launch(["explorer", str(path)])  # opens it in the default PDF viewer
        else:
            self._flash(f"{path.name} is no longer there.")

    def _show_selected_in_folder(self):
        path = self._selected_saved_path()
        if path is not None:
            self._launch(["explorer", "/select,", str(path)])

    def _open_last_output(self):
        if self._last_output_dir is not None:
            target = self._last_output_dir
        elif self.output_mode.get() == "custom" and self.custom_output_dir is not None:
            target = self.custom_output_dir
        else:
            target = pipeline.DEFAULT_OUTPUT_DIR
            target.mkdir(parents=True, exist_ok=True)
        self._launch(["explorer", str(target)])

    def _copy_selected_error(self):
        selection = self.tree.selection()
        if not selection:
            self._flash("Select a row first, then copy its details.")
            return
        row_id = selection[0]
        message = self.full_message_by_row.get(row_id, "(no log captured for this row yet)")
        self.root.clipboard_clear()
        self.root.clipboard_append(message)
        self._flash(f"Copied the details for {self.tree.set(row_id, 'file')}.")

    def _open_log_file(self):
        if not pipeline.LOG_PATH.exists():
            pipeline.LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
            pipeline.LOG_PATH.touch()
        # Popen, not run: waiting for the editor to exit would freeze the window.
        self._launch(["notepad.exe", str(pipeline.LOG_PATH)])

    def _show_help(self):
        """Open the Help window, or bring it forward if it is already open."""
        if self.help_window is not None and self.help_window.winfo_exists():
            self.help_window.focus()
            return
        self.help_window = window = ctk.CTkToplevel(self.root)
        window.title(f"{APP_NAME} help")
        window.geometry("600x620")
        window.minsize(420, 320)
        window.transient(self.root)
        body = ctk.CTkScrollableFrame(window, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=8, pady=(8, 0))
        body.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(body, text=f"{APP_NAME} {__version__}", font=ctk.CTkFont(size=18, weight="bold")).grid(
            row=0, column=0, sticky="w", padx=8, pady=(4, 0)
        )
        for n, (heading, text) in enumerate(HELP_SECTIONS, start=1):
            ctk.CTkLabel(body, text=heading, font=ctk.CTkFont(weight="bold")).grid(
                row=2 * n - 1, column=0, sticky="w", padx=8, pady=(12, 0)
            )
            ctk.CTkLabel(body, text=text, justify="left", anchor="w", wraplength=520).grid(
                row=2 * n, column=0, sticky="w", padx=8
            )
        ctk.CTkButton(window, text="Close", width=90, command=window.destroy).pack(anchor="e", padx=16, pady=12)
        window.bind("<Escape>", lambda e: window.destroy())
        # A new CTkToplevel can open behind its parent on Windows.
        window.after(150, window.lift)
        window.after(150, window.focus)

    def _launch(self, args: list[str]):
        """Start Explorer or Notepad without waiting; the one place the window starts programs."""
        subprocess.Popen(args)


def main():
    root = CTkDnD()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
