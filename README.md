# File to PDF

A small Windows desktop tool that turns files into PDFs. Drop files onto the
window, pick them with the Open dialog, or convert whole folders from the
command line.

Download the Windows app, which needs no Python install, from
[Releases](https://github.com/PrashantKumarChem/file-to-pdf/releases/latest).

- `.ipynb` notebooks are exported by nbconvert with the bundled
  `topdf-notebook` template, so long code lines, output tables and wide
  figures wrap or scale instead of being clipped at the page edge. The PDF is
  the one `nbconvert --to webpdf` produces, printed in this process.
- Every other file is printed 1:1: JSON, code and text exactly as written,
  Markdown as the rendered document. Nothing is added: no heading, no file
  name, no extra sections.
- One Chromium (Playwright) engine prints everything.
- Text is read in UTF-8, UTF-16 or UTF-32 (by byte-order mark) or cp1252.
  Binary files are refused with an error rather than printed as garbage.

## Files

| File | Purpose |
| --- | --- |
| `topdf/gui.py` | CustomTkinter window: drop zone, output options, queue list, worker thread |
| `topdf/cli.py` | Command line: files, folders and wildcards, same output as the window |
| `topdf/pipeline.py` | One file in, one PDF saved: output naming, retries, fallback folder, log |
| `topdf/converters.py` | File type routing, text decoding, notebook export, HTML adapters and the HTML -> PDF engine |
| `topdf/templates/topdf-notebook/` | nbconvert template for notebooks |
| `File to PDF.vbs` | Starts the window from source with `pythonw.exe` and no console window |
| `packaging/` | PyInstaller spec, entry scripts and smoke test for the Windows app |
| `tests/` | pytest suite, including a hidden-window GUI test and Chromium/nbconvert rendering tests |

## Behavior worth knowing

- **Where PDFs go.** `%USERPROFILE%\File to PDF` by default, next to the
  source, or a chosen folder. The choice is taken when a file is added, so
  changing it mid-batch only affects files added afterwards. If the folder
  refuses the write, the PDF goes to `Desktop\File to PDF - could not save`.
- **Names.** `analysis.ipynb` becomes `analysis.pdf`; other files keep their
  extension (`analysis.json.pdf`). Converting the same file again replaces
  its PDF and the status says "replaced existing PDF". A different file that
  would produce the same name in the same session gets a numbered name such as
  `analysis.json (2).pdf`. PDFs from earlier sessions are not tracked, so a
  same-named one is replaced.
- **What the PDF contains.** Only the file's content, on Letter pages: 0.6 in
  margins for files, the template's 0.1 in for notebooks.
  - JSON is highlighted from the source text, never parsed and re-serialized,
    so numbers keep their spelling (`1.0` stays `1.0`) and key order and
    indentation are unchanged. The palette is deliberately muted for pages of
    long string values: bold maroon keys, green strings, purple
    `true`/`false`/`null`, numbers and punctuation in the text color.
  - Code files (anything Pygments has a lexer for) and Markdown code blocks
    use the same palette. Other text files print unhighlighted.
  - Links to local files print without their target, so no local folder path
    ends up inside the PDF (in Markdown files as plain text, in notebooks in
    the link color they always had); web links stay clickable.
- **Web resources.** Printing a notebook loads MathJax and require.js from
  cdnjs, and chart outputs (Altair/Vega) load their libraries from jsdelivr,
  as nbconvert's pages always have. If anything the page asks for doesn't
  load (offline, say), the PDF is still saved, the status starts with
  "Warning: ... web resources did not load", and the log lists them.
- **Batches.** Files queued together (a drop, a multi-select, one
  command-line run) share one Chromium, which closes as soon as the queue is
  empty. If Chromium or its Playwright driver dies partway, both are
  restarted and that file is tried once more.
- **Log.** Each conversion is appended to
  `%LOCALAPPDATA%\File to PDF\conversion_log.txt`; past 1 MB it moves to
  `conversion_log.old.txt`. "Open full log" in the window opens it.

## Command line

The same conversion without the window, for folders, batches and scripts:

```
topdf analysis.ipynb data\*.json
topdf "C:\Projects\results" --recursive --next-to-source
topdf notes.md --out C:\PDFs
```

`topdf` is `topdf.exe` in the Windows app; from source, run `python -m topdf`
with the same arguments.

- **Inputs.** A file named directly always converts; unknown types print as
  text. A folder converts every file that prints: recognized types and
  anything else that reads as text (`.xyz`, `.jdx`, input decks). It skips
  images, spreadsheets and other binary files, PDFs (so an earlier
  `--next-to-source` run isn't reprinted), and hidden entries such as
  `.ipynb_checkpoints`, and lists what it skipped. `--recursive` includes
  subfolders. Wildcards are expanded by the tool, so they work in cmd and
  PowerShell.
- **Output.** PDFs go to `%USERPROFILE%\File to PDF` unless `--out FOLDER`
  or `--next-to-source` says otherwise. Names, the fallback folder, warnings
  and the log are the same as in the window.
- **Result.** Each file prints one status line. The exit code is 0 when
  everything converted, 1 when a file failed or a path matched nothing, and 2
  when there was nothing to convert. `--version` prints the version.
- **Naming across runs.** Each run is a new session, so a later run that
  converts a different `results.json` into the same folder replaces the
  earlier PDF. Convert same-named files in one run to get
  `results.json (2).pdf`.

## Windows app without Python

GitHub Actions builds a standalone copy with PyInstaller
(`packaging/topdf.spec`, workflow `.github/workflows/windows-app.yml`) and
checks it with `packaging/smoke_test.py`. Pushing a `v*` tag attaches
`FileToPDF-windows.zip` to a GitHub release; other runs keep the zip as a
workflow artifact. Download the latest zip from
[Releases](https://github.com/PrashantKumarChem/file-to-pdf/releases/latest).

Unzip it to a short folder path and run the programs below. Chromium sits
about 135 characters deep inside the app folder, and Windows won't start a
program whose path is 260 characters or longer. So keep the folder's own
path under about 120 characters: `C:\Users\<you>\Downloads\FileToPDF` is
fine; a deeply nested synced folder may not be. If the path is too long,
conversions fail with a message saying so.

- `File to PDF.exe`, the window;
- `topdf.exe`, the command line described above. Add the folder to `PATH` to
  use it from any terminal.

It bundles Python, nbconvert's templates and Chromium's headless shell, so
nothing else needs installing. The programs are unsigned, so Windows
SmartScreen asks before the first run. Notebooks still load MathJax and chart
libraries from the web when printed.

To build it locally (PowerShell, from the repository root):

```
pip install -r requirements.txt -r packaging/requirements.txt
$env:PLAYWRIGHT_BROWSERS_PATH = "0"; python -m playwright install --only-shell chromium
python -m PyInstaller --noconfirm packaging/topdf.spec
python packaging/smoke_test.py dist/FileToPDF
```

## Running from source

1. Install Python 3.10 or later (the Windows app is built with 3.12), the
   dependencies, and the Chromium build Playwright uses:

   ```
   pip install -r requirements.txt
   python -m playwright install chromium
   ```

2. Start the window with `python -m topdf.gui` from the repository folder, or
   double-click `File to PDF.vbs`. The launcher starts
   `%USERPROFILE%\Miniconda3\pythonw.exe`; if your Python environment lives
   elsewhere, change that one line.

## Tests

```
pip install -r requirements-dev.txt
python -m pytest                 # everything, about 30 seconds
python -m pytest -m "not slow"   # skip Chromium and nbconvert rendering
```

The GUI tests open a hidden window, so run them from a desktop session.

## Contributing

Bug reports and pull requests are welcome; see
[CONTRIBUTING.md](CONTRIBUTING.md). Report security problems privately as
described in [SECURITY.md](SECURITY.md).

## How this was built

File to PDF was built with Claude Code, Anthropic's coding assistant, with
every change reviewed and tested by the maintainer.
[AI_USAGE.md](AI_USAGE.md) describes how.

## Citing

If you use File to PDF in your work, the "Cite this repository" button on
GitHub gives a citation from [CITATION.cff](CITATION.cff).

## License

[MIT](LICENSE). The Windows app bundles Python, Chromium and other
open-source components, each under its own license.
