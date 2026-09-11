"""
Format adapters + shared HTML->PDF engine for the Notebook/File -> PDF tool.

Architecture: one PDF engine, many small "render to HTML" adapters.
Everything except Jupyter notebooks is turned into a styled HTML string here,
then printed to PDF by the same Chromium/Playwright pipeline that nbconvert's
webpdf exporter uses. Notebooks keep going through nbconvert (see the GUI
module) because its rich-output handling is worth reusing.

Public API:
    html_to_pdf(html_str) -> bytes
    file_to_pdf_bytes(path) -> bytes        # routes by extension
    SUPPORTED_EXTENSIONS                     # set of handled suffixes (no .ipynb)
"""

import asyncio
import concurrent.futures
import html as html_lib
import json
import os
import tempfile
from pathlib import Path

from pygments import highlight
from pygments.formatters import HtmlFormatter
from pygments.lexers import JsonLexer, TextLexer, get_lexer_for_filename

# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------
JSON_EXT = {".json"}
MD_EXT = {".md", ".markdown"}
CODE_EXT = {
    ".py", ".pyw", ".js", ".ts", ".jsx", ".tsx", ".r", ".jl", ".c", ".cc",
    ".cpp", ".h", ".hpp", ".java", ".cs", ".go", ".rb", ".php", ".swift",
    ".kt", ".rs", ".sh", ".bash", ".ps1", ".bat", ".css", ".scss", ".html",
    ".htm", ".xml", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".sql", ".m",
}
TEXT_EXT = {".txt", ".log", ".text", ".dat", ".out", ".csv"}

SUPPORTED_EXTENSIONS = JSON_EXT | MD_EXT | CODE_EXT | TEXT_EXT

_PYGMENTS_CSS = HtmlFormatter(style="default").get_style_defs(".highlight")

# ---------------------------------------------------------------------------
# HTML shell (matches the notebook PDF look: portrait, wrapping, clean font)
# The @page rule is the only page setup: the print call in _render_pdf passes
# prefer_css_page_size and no size or margins of its own.
# ---------------------------------------------------------------------------
_SHELL = """<!DOCTYPE html>
<html><head><meta charset="utf-8">
<style>
@page {{ size: letter portrait; margin: 0.4in; }}
* {{ -webkit-print-color-adjust: exact; }}
body {{
  font-family: "Segoe UI", Arial, sans-serif;
  font-size: 12px; color: #1a1a1a; line-height: 1.45;
}}
h1 {{ font-size: 20px; margin: 0 0 4px; }}
h2 {{ font-size: 15px; margin: 18px 0 6px; color: #333; }}
.subtle {{ color: #888; font-size: 11px; margin: 0 0 16px; }}
pre, code {{
  font-family: "Cascadia Mono", Consolas, "Courier New", monospace;
  font-size: 11px;
}}
pre {{
  white-space: pre-wrap; word-break: break-word; overflow-wrap: anywhere;
  background: #f5f5f5; border: 1px solid #e2e2e2; border-radius: 4px;
  padding: 10px 12px; margin: 0;
}}
.plain {{ background: #fafafa; }}
.highlight {{ background: #f5f5f5; border: 1px solid #e2e2e2; border-radius: 4px; }}
.highlight pre {{ background: transparent; border: 0; }}
table.jsontbl {{
  border-collapse: collapse; margin: 2px 0; width: auto; max-width: 100%;
}}
table.jsontbl th, table.jsontbl td {{
  border: 1px solid #d0d0d0; padding: 3px 8px; text-align: left;
  vertical-align: top; word-break: break-word; overflow-wrap: anywhere;
}}
table.jsontbl th {{ background: #eef2f7; font-weight: 600; white-space: nowrap; }}
table.jsontbl td table.jsontbl {{ margin: 0; }}
.null {{ color: #999; font-style: italic; }}
ul {{ margin: 2px 0 2px 18px; padding: 0; }}
{pygments_css}
</style></head>
<body>{body}</body></html>
"""


def wrap_html(title: str, subtitle: str, body: str) -> str:
    header = f"<h1>{html_lib.escape(title)}</h1>"
    if subtitle:
        header += f"<p class='subtle'>{html_lib.escape(subtitle)}</p>"
    return _SHELL.format(pygments_css=_PYGMENTS_CSS, body=header + body)


# ---------------------------------------------------------------------------
# Adapters: path -> body HTML
# ---------------------------------------------------------------------------
def _json_value_html(obj) -> str:
    """Recursively render any JSON value. Lists of objects become tables."""
    if isinstance(obj, dict):
        rows = "".join(
            f"<tr><th>{html_lib.escape(str(k))}</th>"
            f"<td>{_json_value_html(v)}</td></tr>"
            for k, v in obj.items()
        )
        return f"<table class='jsontbl'>{rows}</table>"
    if isinstance(obj, list):
        if obj and all(isinstance(x, dict) for x in obj):
            keys: list = []
            for d in obj:
                for k in d:
                    if k not in keys:
                        keys.append(k)
            head = "".join(f"<th>{html_lib.escape(str(k))}</th>" for k in keys)
            body = ""
            for d in obj:
                cells = "".join(
                    f"<td>{_json_value_html(d.get(k))}</td>" for k in keys
                )
                body += f"<tr>{cells}</tr>"
            return f"<table class='jsontbl'><tr>{head}</tr>{body}</table>"
        return "<ul>" + "".join(f"<li>{_json_value_html(x)}</li>" for x in obj) + "</ul>"
    if obj is None:
        return "<span class='null'>null</span>"
    if isinstance(obj, bool):
        return html_lib.escape(str(obj))
    return html_lib.escape(str(obj))


def json_to_body(path: Path) -> str:
    raw = path.read_text(encoding="utf-8")
    data = json.loads(raw)
    structured = "<h2>Structured view</h2>" + _json_value_html(data)
    pretty = json.dumps(data, indent=2, ensure_ascii=False)
    highlighted = highlight(pretty, JsonLexer(), HtmlFormatter())
    raw_section = "<h2>Raw JSON</h2>" + highlighted
    return structured + raw_section


def markdown_to_body(path: Path) -> str:
    import markdown

    text = path.read_text(encoding="utf-8")
    return markdown.markdown(
        text,
        extensions=["fenced_code", "tables", "codehilite", "sane_lists"],
        # codehilite defaults to a .codehilite wrapper; point it at the same
        # .highlight class our shell's Pygments CSS styles, so fenced code
        # blocks get syntax colors too.
        extension_configs={"codehilite": {"css_class": "highlight"}},
    )


def code_to_body(path: Path) -> str:
    code = path.read_text(encoding="utf-8", errors="replace")
    try:
        lexer = get_lexer_for_filename(path.name, code)
    except Exception:
        lexer = TextLexer()
    return highlight(code, lexer, HtmlFormatter())


def text_to_body(path: Path) -> str:
    text = path.read_text(encoding="utf-8", errors="replace")
    return f"<pre class='plain'>{html_lib.escape(text)}</pre>"


def _route(path: Path):
    ext = path.suffix.lower()
    if ext in JSON_EXT:
        return json_to_body, "JSON"
    if ext in MD_EXT:
        return markdown_to_body, "Markdown"
    if ext in CODE_EXT:
        return code_to_body, "Code"
    return text_to_body, "Text"  # default: never hard-fail


def file_to_html(path: Path) -> str:
    builder, kind = _route(path)
    body = builder(path)
    return wrap_html(path.name, f"{kind} - {path}", body)


# ---------------------------------------------------------------------------
# Engine: HTML string -> PDF bytes, via the same Chromium path nbconvert uses
# ---------------------------------------------------------------------------
async def _render_pdf(url: str) -> bytes:
    from playwright.async_api import async_playwright

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            handle_sigint=False, handle_sigterm=False, handle_sighup=False
        )
        try:
            page = await browser.new_page()
            await page.emulate_media(media="print")
            await page.goto(url, wait_until="networkidle")
            return await page.pdf(print_background=True, prefer_css_page_size=True)
        finally:
            await browser.close()


def _run_in_fresh_loop(coro):
    # Playwright drives Chromium through subprocess pipes, which on Windows need
    # the Proactor loop; make it explicit in case something set another policy.
    loop = asyncio.ProactorEventLoop() if os.name == "nt" else asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def html_to_pdf(html: str) -> bytes:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        page_path = Path(tmp) / "page.html"
        page_path.write_text(html, encoding="utf-8")
        # A private thread with its own event loop, so this also works when the
        # calling thread already runs one (inside Jupyter, for example). The
        # with-block joins the thread; nothing outlives the call.
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(_run_in_fresh_loop, _render_pdf(page_path.as_uri())).result()


def file_to_pdf_bytes(path: Path) -> bytes:
    return html_to_pdf(file_to_html(path))
