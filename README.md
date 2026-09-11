# File -> PDF

A small Windows desktop tool that turns files into PDFs. Drop files onto the
window or pick them with the Open dialog.

- `.ipynb` notebooks go through `nbconvert --to webpdf` with the custom
  `pdf-nowrap-fix` template, so long code lines, output tables and wide figures
  wrap or scale instead of being clipped at the page edge.
- JSON and Markdown are rendered to styled HTML by `converters.py`; anything
  Pygments has a lexer for is syntax-highlighted; every other text file is
  printed as plain text. All of it goes to PDF through Chromium (Playwright).
- Text is read in UTF-8, UTF-16 or UTF-32 (by byte-order mark) or cp1252.
  Binary files are refused with an error rather than printed as garbage.

## Files

| File | Purpose |
| --- | --- |
| `notebook_to_pdf_gui.py` | CustomTkinter window: drop zone, output options, queue list, worker thread |
| `pipeline.py` | One file in, one PDF saved: nbconvert for notebooks, retries, fallback folder, output naming, log |
| `converters.py` | File type routing, text decoding, HTML adapters and the HTML -> PDF engine |
| `Launch Notebook to PDF.vbs` | Starts the GUI with `pythonw.exe` and no console window (the desktop shortcut points here) |
| `nbconvert-templates/pdf-nowrap-fix/` | nbconvert template for the notebook path, loaded from this folder |
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
- **What the PDF contains.** Non-notebook PDFs show the file name and its kind
  (JSON, Markdown, Code, Text), not the folder it came from.
- **Log.** Each conversion is appended to `conversion_log.txt` next to the
  scripts; past 1 MB it moves to `conversion_log.old.txt`. Neither is tracked,
  since they record the full paths of converted files.

## Restoring on a machine

1. Install the dependencies and the Chromium build Playwright uses:

   ```
   pip install -r requirements.txt
   playwright install chromium
   ```

2. `Launch Notebook to PDF.vbs` starts `%USERPROFILE%\Miniconda3\pythonw.exe`.
   If the environment lives elsewhere, change that one line. The GUI runs
   nbconvert with the same interpreter it was started with.

## Tests

```
pip install -r requirements-dev.txt
python -m pytest            # everything, about 20 seconds
python -m pytest -m "not slow"   # skip Chromium and nbconvert rendering
```

The GUI tests open a hidden window, so run them from a desktop session.
