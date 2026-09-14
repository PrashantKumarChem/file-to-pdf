# File -> PDF

A small Windows desktop tool that turns files into PDFs. Drop files onto the
window, pick them with the Open dialog, or convert whole folders from the
command line.

- `.ipynb` notebooks are exported by nbconvert with the custom
  `pdf-nowrap-fix` template, so long code lines, output tables and wide figures
  wrap or scale instead of being clipped at the page edge. The PDF is the one
  `nbconvert --to webpdf` produces, printed in this process.
- Every other file is printed 1:1: JSON, code and text exactly as written,
  Markdown as the rendered document. Nothing is added: no heading, no file
  name, no extra sections.
- One Chromium (Playwright) engine prints everything.
- Text is read in UTF-8, UTF-16 or UTF-32 (by byte-order mark) or cp1252.
  Binary files are refused with an error rather than printed as garbage.

## Files

| File | Purpose |
| --- | --- |
| `notebook_to_pdf_gui.py` | CustomTkinter window: drop zone, output options, queue list, worker thread |
| `notebook_to_pdf_cli.py` | Command line: files, folders and wildcards, same output as the window |
| `pipeline.py` | One file in, one PDF saved: output naming, retries, fallback folder, log |
| `converters.py` | File type routing, text decoding, notebook export, HTML adapters and the HTML -> PDF engine |
| `Launch Notebook to PDF.vbs` | Starts the GUI with `pythonw.exe` and no console window (the desktop shortcut points here) |
| `nbconvert-templates/pdf-nowrap-fix/` | nbconvert template for notebooks, loaded from this folder |
| `tests/` | pytest suite, including a hidden-window GUI test and Chromium/nbconvert rendering tests |

## Behavior worth knowing

- **Where PDFs go.** `%USERPROFILE%\Notebook PDFs` by default, next to the
  source, or a chosen folder. The choice is taken when a file is added, so
  changing it mid-batch only affects files added afterwards. If the folder
  refuses the write, the PDF goes to `Desktop\NotebookToPDF - could not save`.
- **Names.** `Compound1.ipynb` becomes `Compound1.pdf`; other files keep their
  extension (`Compound1.json.pdf`). Converting the same file again replaces its
  PDF and the status says "replaced existing PDF". A different file that would
  produce the same name in the same session gets `Compound1 (2).pdf`. PDFs from
  earlier sessions are not tracked, so a same-named one is replaced.
- **What the PDF contains.** Only the file's content, on Letter pages: 0.6 in
  margins for files, the template's 0.1 in for notebooks.
  - JSON is highlighted from the source text, never parsed and re-serialized,
    so numbers keep their spelling (`1.0` stays `1.0`) and key order and
    indentation are unchanged. The palette is deliberately muted for pages of
    long InChI/SMILES strings: bold maroon keys, green strings, purple
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
- **Log.** Each conversion is appended to `conversion_log.txt` next to the
  scripts; past 1 MB it moves to `conversion_log.old.txt`. Neither is tracked,
  since they record the full paths of converted files.

## Command line

The same conversion without the window, for folders, batches and scripts:

```
python notebook_to_pdf_cli.py Compound1.ipynb data\*.json
python notebook_to_pdf_cli.py "D:\Paper\SI files" --recursive --next-to-source
python notebook_to_pdf_cli.py notes.md --out C:\PDFs
```

- **Inputs.** A file named directly always converts; unknown types print as
  text. A folder converts every file that prints: recognized types and
  anything else that reads as text (`.xyz`, `.jdx`, input decks). It skips
  images, spreadsheets and other binary files, PDFs (so an earlier
  `--next-to-source` run isn't reprinted), and hidden entries such as
  `.ipynb_checkpoints`, and lists what it skipped. `--recursive` includes
  subfolders. Wildcards are expanded by the tool, so they work in cmd and
  PowerShell.
- **Output.** PDFs go to `%USERPROFILE%\Notebook PDFs` unless `--out FOLDER`
  or `--next-to-source` says otherwise. Names, the fallback folder, warnings
  and the log are the same as in the window.
- **Result.** Each file prints one status line. The exit code is 0 when
  everything converted, 1 when a file failed or a path matched nothing, and 2
  when there was nothing to convert.
- **Naming across runs.** Each run is a new session, so a later run that
  converts a different `Compound1.json` into the same folder replaces the
  earlier PDF. Convert same-named files in one run to get
  `Compound1.json (2).pdf`.

## Restoring on a machine

1. Install the dependencies and the Chromium build Playwright uses:

   ```
   pip install -r requirements.txt
   playwright install chromium
   ```

2. `Launch Notebook to PDF.vbs` starts `%USERPROFILE%\Miniconda3\pythonw.exe`.
   If the environment lives elsewhere, change that one line.

## Tests

```
pip install -r requirements-dev.txt
python -m pytest                 # everything, about 30 seconds
python -m pytest -m "not slow"   # skip Chromium and nbconvert rendering
```

The GUI tests open a hidden window, so run them from a desktop session.
