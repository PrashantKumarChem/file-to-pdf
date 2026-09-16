"""Drive the real window (withdrawn) through its queue, with the PDF engine stubbed."""

import contextlib
import gc
import threading
import time

import pytest

from topdf import converters, pipeline

gui = pytest.importorskip("topdf.gui")


@pytest.fixture
def app(isolated_pipeline, monkeypatch):
    monkeypatch.setattr(converters, "render", lambda path: converters.Rendered(b"%PDF-1.7 fake", []))
    root = gui.CTkDnD()
    root.withdraw()
    # Tk prints exceptions raised in callbacks and carries on; record them so a
    # broken _poll_results fails the test instead of scrolling past.
    callback_errors = []
    root.report_callback_exception = lambda exc, value, tb: callback_errors.append(value)
    application = gui.App(root)
    application.auto_open.set(False)
    # Record the programs the window would start instead of starting them.
    application.launched = []
    application._launch = application.launched.append
    yield application
    root.destroy()
    # Finalize the dead window's Tk objects (fonts, images) here, on the Tk
    # thread. Left to a later garbage collection on a render thread, each
    # Font.__del__ waits on a Tcl call that no mainloop will serve, which made
    # the next Chromium test take 15-27 s instead of about 1.5 s.
    gc.collect()
    assert callback_errors == []


def run_until_settled(app, timeout=30):
    """Run the real mainloop until no row is queued or converting."""
    deadline = time.monotonic() + timeout

    def check():
        statuses = [app.tree.set(r, "status") for r in app.tree.get_children()]
        busy = any(s.startswith(("Queued", "Converting")) for s in statuses)
        if not busy or time.monotonic() > deadline:
            app.root.quit()
        else:
            app.root.after(50, check)

    app.root.after(50, check)
    app.root.mainloop()
    return [(app.tree.set(r, "file"), app.tree.set(r, "status")) for r in app.tree.get_children()]


def saved_column(app):
    return [app.tree.set(r, "saved") for r in app.tree.get_children()]


def write(path, text="{}"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_queue_survives_errors_and_duplicates(app, monkeypatch, tmp_path):
    real_save = pipeline._save_pdf_bytes

    def save(data, stem, *rest):
        if stem.startswith("boom"):
            raise RuntimeError("injected failure")
        return real_save(data, stem, *rest)

    monkeypatch.setattr(pipeline, "_save_pdf_bytes", save)
    a = write(tmp_path / "src" / "a.json")
    boom = write(tmp_path / "src" / "boom.json")
    b = write(tmp_path / "src" / "b.md", "# B")

    app._enqueue_paths([str(a), str(boom), str(b), str(a)])
    rows = run_until_settled(app)

    out = pipeline.DEFAULT_OUTPUT_DIR
    assert rows == [
        ("a.json", "✓ Done"),
        ("boom.json", "✕ Failed: injected failure"),
        ("b.md", "✓ Done"),
        ("a.json", "✓ Done, replaced existing PDF"),
    ]
    assert saved_column(app) == [str(out / "a.json.pdf"), "", str(out / "b.md.pdf"), str(out / "a.json.pdf")]
    assert [app.tree.item(r, "tags") for r in app.tree.get_children()] == [
        ("done",),
        ("failed",),
        ("done",),
        ("done",),
    ]
    assert (
        app.summary_label.cget("text")
        == "3 converted, 1 failed  ·  right-click a failed file and Copy details to see why"
    )


def test_output_option_is_fixed_when_files_are_added(app, tmp_path):
    src = write(tmp_path / "src" / "a.json")
    app._enqueue_paths([str(src)])
    app.output_mode.set("same")  # changed after queueing
    run_until_settled(app)
    assert saved_column(app) == [str(pipeline.DEFAULT_OUTPUT_DIR / "a.json.pdf")]
    assert not list(src.parent.glob("*.pdf"))


def test_cancelled_folder_picker_restores_previous_option(app, monkeypatch):
    monkeypatch.setattr(gui.filedialog, "askdirectory", lambda **kwargs: "")
    app.output_mode.set("custom")
    app._choose_output_dir()
    assert app.output_mode.get() == "local"


def test_results_for_cleared_rows_are_ignored(app, tmp_path):
    src = write(tmp_path / "src" / "a.json")
    app._enqueue_paths([str(src)])
    app._clear_list()
    app.root.after(1000, app.root.quit)
    app.root.mainloop()  # the cleared row's results arrive here
    assert app.tree.get_children() == ()
    # The list keeps working after a clear.
    app._enqueue_paths([str(src)])
    rows = run_until_settled(app)
    assert [status for _, status in rows] == ["✓ Done, replaced existing PDF"]


def test_drop_payload_with_spaces(app, tmp_path):
    spaced = write(tmp_path / "dir with spaces" / "a b.json")
    plain = write(tmp_path / "plain.json")
    missing = tmp_path / "missing.json"
    payload = f"{{{spaced.as_posix()}}} {plain.as_posix()} {missing.as_posix()}"
    assert [p.replace("\\", "/") for p in app._parse_drop(payload)] == [
        spaced.as_posix(),
        plain.as_posix(),
    ]


def test_files_queued_together_share_one_batch(app, monkeypatch, tmp_path):
    events = []

    @contextlib.contextmanager
    def batch():
        events.append("open")
        yield
        events.append("close")

    def render(path):
        time.sleep(0.2)  # long enough for the whole drop to be queued
        events.append(path.name)
        return converters.Rendered(b"%PDF-1.7 fake", [])

    monkeypatch.setattr(converters, "batch", batch)
    monkeypatch.setattr(converters, "render", render)
    app._enqueue_paths([str(write(tmp_path / "src" / f"{name}.json")) for name in "abc"])
    run_until_settled(app)
    deadline = time.monotonic() + 5
    while events[-1:] != ["close"] and time.monotonic() < deadline:
        time.sleep(0.05)  # the batch closes just after the last result
    assert events == ["open", "a.json", "b.json", "c.json", "close"]


def test_unrecognized_files_are_marked_as_text(app, tmp_path):
    known = write(tmp_path / "run.log", "x")
    unknown = write(tmp_path / "mystery.zzz", "x")
    app._enqueue_paths([str(known), str(unknown)])
    statuses = [app.tree.set(r, "status") for r in app.tree.get_children()]
    assert statuses == ["Queued", "Queued (as text)"]
    run_until_settled(app)


def test_short_status_leaves_the_path_to_its_own_column(tmp_path):
    pdf = tmp_path / "a.json.pdf"
    fallback = tmp_path / "fallback" / "a.json.pdf"
    cases = [
        (pipeline.Result(True, f"Done -> {pdf}", "", pdf), "Done", gui.DONE),
        (
            pipeline.Result(True, f"Done, replaced existing PDF -> {pdf}", "", pdf),
            "Done, replaced existing PDF",
            gui.DONE,
        ),
        (
            pipeline.Result(
                True, f"Warning: 1 web resource did not load; charts or math may be missing. Done -> {pdf}", "", pdf
            ),
            "Warning: 1 web resource did not load; charts or math may be missing. Done",
            gui.WARNING,
        ),
        (
            pipeline.Result(True, f"Saved to fallback (couldn't write {tmp_path}): {fallback}", "", fallback),
            f"Saved to fallback (couldn't write {tmp_path})",
            gui.WARNING,
        ),
        (pipeline.Result(False, "Failed: injected failure", "", None), "Failed: injected failure", gui.FAILED),
    ]
    for result, status, state in cases:
        assert (gui.short_status(result), gui.state_of(result)) == (status, state)


def test_elide_keeps_both_ends():
    assert gui.elide("short", 10) == "short"
    shortened = gui.elide(r"C:\a very long folder name\PDFs", 15)
    assert len(shortened) == 15
    assert shortened.startswith(r"C:\a v")
    assert shortened.endswith(r"\PDFs")


def test_dropped_folder_adds_the_files_that_print(app, tmp_path):
    folder = tmp_path / "results"
    write(folder / "a.json")
    write(folder / "b.md", "# B")
    (folder / "old.pdf").write_bytes(b"%PDF-1.7")
    write(folder / ".hidden.json")
    write(folder / "sub" / "c.json")
    app._enqueue_paths([str(folder)])
    assert app.summary_label.cget("text") == "Left out 1 file that won't print (PDFs, images and other binary files)."
    rows = run_until_settled(app)
    assert rows == [("a.json", "✓ Done"), ("b.md", "✓ Done")]


def test_folder_with_nothing_to_print_says_so(app, tmp_path):
    folder = tmp_path / "empty"
    folder.mkdir()
    app._enqueue_paths([str(folder)])
    assert app.tree.get_children() == ()
    assert app.summary_label.cget("text").startswith("Nothing to convert")


def test_removed_rows_are_not_converted(app, monkeypatch, tmp_path):
    started = threading.Event()
    release = threading.Event()
    rendered = []

    def render(path):
        rendered.append(path.name)
        started.set()
        release.wait(10)
        return converters.Rendered(b"%PDF-1.7 fake", [])

    monkeypatch.setattr(converters, "render", render)
    app._enqueue_paths([str(write(tmp_path / "src" / f"{name}.json")) for name in "abc"])
    assert started.wait(10)
    _first, second, _third = app.tree.get_children()
    app.tree.selection_set(second)
    app._remove_selected()
    app._clear_list()
    assert app.summary_label.cget("text") == ""
    release.set()
    app._enqueue_paths([str(write(tmp_path / "src" / "d.json"))])
    rows = run_until_settled(app)
    assert rows == [("d.json", "✓ Done")]
    assert rendered == ["a.json", "d.json"]


def test_summary_and_progress_follow_the_rows(app, tmp_path):
    assert app.empty_label.winfo_manager() == "place"
    app._enqueue_paths([str(write(tmp_path / "src" / f"{name}.json")) for name in "ab"])
    assert app.empty_label.winfo_manager() == ""
    assert app.summary_label.cget("text") == "Converting… 0 of 2 finished"
    assert app.progress.winfo_manager() == "grid"
    run_until_settled(app)
    assert app.summary_label.cget("text") == "2 converted  ·  double-click a file to open its PDF"
    assert app.progress.winfo_manager() == ""


def test_row_actions_open_copy_and_show(app, tmp_path):
    app._enqueue_paths([str(write(tmp_path / "src" / "a.json"))])
    run_until_settled(app)
    (row,) = app.tree.get_children()
    pdf = pipeline.DEFAULT_OUTPUT_DIR / "a.json.pdf"

    app._copy_selected_error()
    assert app.summary_label.cget("text") == "Select a row first, then copy its details."
    app._open_selected_pdf()
    assert app.launched == []

    app.tree.selection_set(row)
    app._copy_selected_error()
    assert "Saved to" in app.root.clipboard_get()
    assert app.summary_label.cget("text") == "Copied the details for a.json."
    app._open_selected_pdf()
    app._show_selected_in_folder()
    app._open_last_output()
    assert app.launched == [
        ["explorer", str(pdf)],
        ["explorer", "/select,", str(pdf)],
        ["explorer", str(pipeline.DEFAULT_OUTPUT_DIR)],
    ]
    pdf.unlink()
    app._open_selected_pdf()
    assert app.summary_label.cget("text") == "a.json.pdf is no longer there."


def test_open_output_folder_before_any_conversion(app):
    app._open_last_output()
    assert app.launched == [["explorer", str(pipeline.DEFAULT_OUTPUT_DIR)]]
    assert pipeline.DEFAULT_OUTPUT_DIR.is_dir()


def test_output_options_show_the_destination(app, monkeypatch, tmp_path):
    assert app.output_label.cget("text") == gui.elide(str(pipeline.DEFAULT_OUTPUT_DIR), 44)
    app.output_mode.set("same")
    app._on_output_mode()
    assert app.output_label.cget("text") == "The folder each file is in"
    chosen = tmp_path / "chosen"
    monkeypatch.setattr(gui.filedialog, "askdirectory", lambda **kwargs: str(chosen))
    app.output_mode.set("custom")
    app._on_output_mode()
    assert app.custom_output_dir == chosen
    assert app.output_label.cget("text") == gui.elide(str(chosen), 44)
    assert app.change_button.winfo_manager() == "pack"
    # Choosing the option again reuses the folder instead of asking again.
    monkeypatch.setattr(gui.filedialog, "askdirectory", lambda **kwargs: pytest.fail("asked again"))
    app.output_mode.set("local")
    app._on_output_mode()
    assert app.change_button.winfo_manager() == ""
    app.output_mode.set("custom")
    app._on_output_mode()
    assert app.output_mode.get() == "custom"


def test_theme_switch_restyles_the_list(app):
    for choice in ("Dark", "Light", "System"):
        app._change_appearance(choice)
    app._on_drag_enter(type("Event", (), {"action": "copy"})())
    assert app.drop_label.cget("text") == "Release to add"
    app._on_drag_leave()
    assert app.drop_label.cget("text") == "Drop files or folders here"


def settle(app, ms=400):
    """Let scheduled callbacks run (CTkToplevel sets its icon and focus a moment after opening)."""
    app.root.after(ms, app.root.quit)
    app.root.mainloop()


def test_help_window_opens_once(app):
    app._show_help()
    settle(app)
    first = app.help_window
    assert first is not None and first.title() == "File to PDF help"
    app._show_help()
    assert app.help_window is first
    first.destroy()
    app._show_help()
    settle(app)
    assert app.help_window is not first
    app.help_window.destroy()


def test_tooltip_appears_and_goes(app):
    tip = gui.Tooltip(app.summary_label, "An explanation.")
    tip.show()
    assert tip._tip is not None and tip._tip.winfo_exists()
    tip._hide()
    assert tip._tip is None


def test_empty_list_describes_what_the_window_does(app):
    text = app.empty_label.cget("text")
    for capability in ("folders", "Ctrl+O", "Notebooks", "JSON, Markdown, code and text", "Double-click", "F1"):
        assert capability in text
