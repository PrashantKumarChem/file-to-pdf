# File -> PDF

A small Windows desktop tool that turns files into PDFs. Drop files onto the
window or pick them with the Open dialog.

- `.ipynb` notebooks go through `jupyter nbconvert --to webpdf` with the custom
  `pdf-nowrap-fix` template, so long code lines, output tables and wide figures
  wrap or scale instead of being clipped at the page edge.
- JSON, Markdown, source code and plain text/log files are rendered to styled
  HTML by `converters.py` and printed to PDF with the same Chromium engine
  (Playwright). Unknown extensions are treated as text.

## Files

| File | Purpose |
| --- | --- |
| `notebook_to_pdf_gui.py` | CustomTkinter GUI, queue/worker, notebook conversion, save with retry and fallback |
| `converters.py` | HTML adapters for non-notebook formats and the HTML -> PDF engine |
| `Launch Notebook to PDF.vbs` | Starts the GUI with `pythonw.exe` and no console window (the desktop shortcut points here) |
| `nbconvert-templates/pdf-nowrap-fix/` | Copy of the nbconvert template the notebook path needs |

## Restoring on a machine

1. Install the dependencies and the Chromium build Playwright uses:

   ```
   pip install -r requirements.txt
   playwright install chromium
   ```

2. Copy `nbconvert-templates/pdf-nowrap-fix` to
   `%USERPROFILE%\.jupyter\nbconvert-templates\pdf-nowrap-fix`.

3. The Python interpreter is expected at `%USERPROFILE%\Miniconda3`
   (`PYTHON_EXE` at the top of `notebook_to_pdf_gui.py`, and
   `Launch Notebook to PDF.vbs`). Adjust both if Python lives elsewhere.

PDFs are saved to `%USERPROFILE%\Notebook PDFs` by default. Conversion details go to
`conversion_log.txt` next to the script; that file and the `_staging` folder are
not tracked.
