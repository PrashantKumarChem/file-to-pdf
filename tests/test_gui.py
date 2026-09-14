"""Drive the real window (withdrawn) through its queue, with the PDF engine stubbed."""

import contextlib
import gc
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
        ("a.json", f"Done -> {out / 'a.json.pdf'}"),
        ("boom.json", "Failed: injected failure"),
        ("b.md", f"Done -> {out / 'b.md.pdf'}"),
        ("a.json", f"Done, replaced existing PDF -> {out / 'a.json.pdf'}"),
    ]


def test_output_option_is_fixed_when_files_are_added(app, tmp_path):
    src = write(tmp_path / "src" / "a.json")
    app._enqueue_paths([str(src)])
    app.output_mode.set("same")  # changed after queueing
    rows = run_until_settled(app)
    assert rows == [("a.json", f"Done -> {pipeline.DEFAULT_OUTPUT_DIR / 'a.json.pdf'}")]
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
    assert [status.split(" -> ")[0] for _, status in rows] == ["Done, replaced existing PDF"]


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
