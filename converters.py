"""
Format adapters + shared HTML->PDF engine for the Notebook/File -> PDF tool.

Architecture: one PDF engine, many small "render to HTML" adapters.
Every file, notebooks included, is turned into an HTML string here and printed
to PDF by one Chromium/Playwright engine. Notebooks are exported by nbconvert's
HTML exporter with the pdf-nowrap-fix template and printed the way nbconvert's
webpdf exporter prints them, so their PDFs are the ones `nbconvert --to webpdf`
produced, without a second Python process.

Public API:
    render(path) -> Rendered                # routes by kind_for(path): PDF + failed web requests
    file_to_pdf_bytes(path) -> bytes
    html_to_pdf(html_str) -> bytes
    kind_for(path) -> "Notebook" | "JSON" | "Markdown" | "Code" | "Text"
    is_recognized(path) -> bool             # False: printed as plain text by default
    dialog_patterns() -> list[str]          # glob patterns for a file picker
    UnsupportedFileError                    # raised for binary input
"""

import asyncio
import codecs
import concurrent.futures
import html as html_lib
import os
import re
import tempfile
from pathlib import Path
from typing import NamedTuple

from pygments import highlight
from pygments.formatters import HtmlFormatter
from pygments.lexers import JsonLexer, TextLexer, get_all_lexers, get_lexer_for_filename
from pygments.style import Style
from pygments.token import Comment, Keyword, Name, String
from pygments.util import ClassNotFound

# ---------------------------------------------------------------------------
# Routing. Which files count as code is Pygments' decision, not a list here.
# ---------------------------------------------------------------------------
NOTEBOOK_EXT = {".ipynb"}
JSON_EXT = {".json"}
MD_EXT = {".md", ".markdown"}
# Plain-text formats Pygments has no lexer for, so the picker offers them and
# the GUI doesn't mark them as unrecognized.
PLAIN_TEXT_EXT = {".txt", ".log", ".text", ".dat", ".out", ".csv"}

_SIMPLE_GLOB = re.compile(r"\*\.[A-Za-z0-9_+-]+")


class UnsupportedFileError(ValueError):
    """The file can't be printed as text (it is binary)."""


def _lexer_for(path: Path, text: str | None = None, **options):
    """Pygments lexer for the file name, or None if it is unknown or plain text.

    Pygments matches its filename globs case-sensitively, so SCRIPT.PY is
    retried with a lower-case suffix.
    """
    for name in dict.fromkeys((path.name, path.with_suffix(path.suffix.lower()).name)):
        try:
            lexer = get_lexer_for_filename(name, text, **options)
        except ClassNotFound:
            continue
        return None if isinstance(lexer, TextLexer) else lexer
    return None


def kind_for(path: Path) -> str:
    ext = path.suffix.lower()
    if ext in NOTEBOOK_EXT:
        return "Notebook"
    if ext in JSON_EXT:
        return "JSON"
    if ext in MD_EXT:
        return "Markdown"
    return "Code" if _lexer_for(path) is not None else "Text"


def is_recognized(path: Path) -> bool:
    return kind_for(path) != "Text" or path.suffix.lower() in PLAIN_TEXT_EXT


def dialog_patterns() -> list[str]:
    """Glob patterns for every file type handled here, for a file picker filter."""
    patterns = {"*" + ext for ext in NOTEBOOK_EXT | JSON_EXT | MD_EXT | PLAIN_TEXT_EXT}
    for _name, _aliases, filenames, _mimetypes in get_all_lexers():
        patterns.update(p for p in filenames if _SIMPLE_GLOB.fullmatch(p))
    return sorted(patterns)


def read_text(path: Path) -> str:
    """Decode a text file whatever its encoding; raise for binary files.

    A byte-order mark wins (UTF-8, UTF-16 and UTF-32 all carry one when
    Windows tools write them). Otherwise UTF-8, falling back to cp1252 for
    older Windows text. NUL bytes without a BOM mean binary. Line endings are
    normalized to \\n, as text-mode reading would.
    """
    data = path.read_bytes()
    for bom, encoding in (
        (codecs.BOM_UTF32_LE, "utf-32"), (codecs.BOM_UTF32_BE, "utf-32"),
        (codecs.BOM_UTF8, "utf-8-sig"),
        (codecs.BOM_UTF16_LE, "utf-16"), (codecs.BOM_UTF16_BE, "utf-16"),
    ):
        if data.startswith(bom):
            text = data.decode(encoding, errors="replace")
            break
    else:
        if b"\x00" in data[:8192]:
            raise UnsupportedFileError(f"{path.name} is a binary file, not text")
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            text = data.decode("cp1252", errors="replace")
    return text.replace("\r\n", "\n").replace("\r", "\n")


class PaperStyle(Style):
    """The syntax palette for every highlighted file: JSON, code, Markdown code.

    Deliberately muted: the JSON notebook pages are mostly long InChI and
    SMILES strings, and editor-bright colors on those tire the eye over many
    pages. Keys (and markup tags) bold maroon, strings green, true/false/null
    and other keywords purple, comments grey; numbers and punctuation stay in
    the body text color.
    """

    background_color = "#ffffff"
    styles = {
        Name.Tag: "bold #9b2158",
        String: "#0f7d33",
        Keyword: "#7a3fc4",
        Comment: "italic #6e6e73",
    }


_PYGMENTS_CSS = HtmlFormatter(style=PaperStyle).get_style_defs(".source")

# ---------------------------------------------------------------------------
# HTML shell: Letter pages with 0.6in margins, content only, no header.
# The @page rule is the only page setup: the print call in _render_pdf passes
# prefer_css_page_size and no size or margins of its own.
# ---------------------------------------------------------------------------
_SHELL = """<!DOCTYPE html>
<html><head><meta charset="utf-8">
{base}<style>
@page {{ size: letter portrait; margin: 0.6in; }}
* {{ -webkit-print-color-adjust: exact; }}
body {{
  margin: 0;
  font-family: "Segoe UI", Arial, sans-serif;
  font-size: 12px; line-height: 1.45; color: #1d1d1f;
}}
.source pre, code {{
  font-family: "SFMono-Regular", Consolas, Menlo, monospace;
}}
.source pre {{
  font-size: 9.5pt; line-height: 1.55; margin: 0;
  white-space: pre-wrap; word-break: break-word; overflow-wrap: anywhere;
}}
.markdown .source {{ margin: 0.8em 0; }}
.markdown table {{ border-collapse: collapse; }}
.markdown th, .markdown td {{ border: 1px solid #d0d0d0; padding: 3px 8px; text-align: left; }}
{pygments_css}
</style></head>
<body>{body}</body></html>
"""


def wrap_html(body: str, base_dir: Path | None = None) -> str:
    """Wrap body HTML in the page shell. Nothing is added around the content.

    base_dir becomes the document base URL, so relative links in the body
    (a Markdown image next to its .md file) resolve from the source folder
    rather than from the temporary file Chromium actually loads.
    """
    base = ""
    if base_dir is not None:
        base = f'<base href="{html_lib.escape(base_dir.absolute().as_uri() + "/")}">\n'
    return _SHELL.format(base=base, pygments_css=_PYGMENTS_CSS, body=body)


# ---------------------------------------------------------------------------
# Adapters: path -> body HTML. Each prints the file 1:1: JSON, code and text
# exactly as written (no reformatting, no added sections), Markdown rendered.
# ---------------------------------------------------------------------------
_FORMATTER = HtmlFormatter(style=PaperStyle, cssclass="source")
# Pygments strips leading and trailing blank lines by default; keep them.
_VERBATIM = {"stripnl": False, "ensurenl": False}


def json_to_body(path: Path) -> str:
    # Highlighted from the source text, never parsed and re-serialized, so
    # numbers keep their spelling (1.0, 1.50e3) and key order and whitespace
    # stay as the file has them. Invalid JSON prints as it is too.
    return highlight(read_text(path), JsonLexer(**_VERBATIM), _FORMATTER)


def markdown_to_body(path: Path) -> str:
    import markdown

    html = markdown.markdown(
        read_text(path),
        extensions=["fenced_code", "tables", "codehilite", "sane_lists"],
        # Fenced code blocks use the same wrapper class, and so the same
        # palette, as code files.
        extension_configs={"codehilite": {"css_class": "source"}},
    )
    return f"<div class='markdown'>{html}</div>"


def code_to_body(path: Path) -> str:
    code = read_text(path)
    # Passing the text lets Pygments pick between lexers sharing a suffix (.m).
    lexer = _lexer_for(path, code, **_VERBATIM) or TextLexer(**_VERBATIM)
    return highlight(code, lexer, _FORMATTER)


def text_to_body(path: Path) -> str:
    # HTML drops one newline right after <pre>; the extra one keeps a file's
    # leading blank line.
    return f"<div class='source'><pre>\n{html_lib.escape(read_text(path))}</pre></div>"


_BUILDERS = {
    "JSON": json_to_body,
    "Markdown": markdown_to_body,
    "Code": code_to_body,
    "Text": text_to_body,  # the default: anything that isn't binary prints
}

# ---------------------------------------------------------------------------
# Notebooks: nbconvert's HTML exporter with the template in this repo
# ---------------------------------------------------------------------------
TEMPLATE_BASE_DIR = Path(__file__).parent / "nbconvert-templates"
TEMPLATE_NAME = "pdf-nowrap-fix"


def notebook_to_html(path: Path) -> str:
    """The notebook as the complete HTML page `nbconvert --to webpdf` printed.

    Same exporter, template and settings as that command, run in this
    process, so the page is byte-for-byte the one the command printed. Unlike
    the command, no Jupyter config files are read, so the output doesn't
    depend on what ~/.jupyter happens to contain.
    """
    from nbconvert.exporters import HTMLExporter
    from traitlets.config import Config

    config = Config()
    config.TemplateExporter.template_name = TEMPLATE_NAME
    config.TemplateExporter.extra_template_basedirs = [str(TEMPLATE_BASE_DIR)]
    html, _resources = HTMLExporter(config=config).from_filename(str(path))
    return html


def file_to_html(path: Path) -> str:
    if kind_for(path) == "Notebook":
        return notebook_to_html(path)
    return wrap_html(_BUILDERS[kind_for(path)](path), base_dir=path.parent)


# ---------------------------------------------------------------------------
# Engine: HTML string -> PDF bytes through Chromium
# ---------------------------------------------------------------------------
class Rendered(NamedTuple):
    """A printed PDF, plus the web (http/https) resources the page failed to load."""

    pdf: bytes
    failed_requests: list[str]


# Runs after load, before printing. A link to a local file is dead in a PDF,
# and Chromium would store its absolute file:// target (a local folder path)
# in the file, so those hrefs are removed and the link text stays. In-page
# #anchors also resolve to file:// URLs but print as internal jumps, so they
# are kept. With keepLook the link keeps its color and underline, so a
# notebook prints exactly as it did before the links were removed; Markdown
# files print them as plain text.
_UNLINK_LOCAL_FILES_JS = """(keepLook) => {
  for (const a of document.querySelectorAll('a[href]')) {
    if (a.protocol === 'file:' && !a.getAttribute('href').startsWith('#')) {
      if (keepLook) {
        const style = getComputedStyle(a);
        a.style.color = style.color;
        a.style.textDecoration = style.textDecoration;
      }
      a.removeAttribute('href');
    }
  }
}"""


async def _render_pdf(url: str, notebook: bool) -> Rendered:
    from playwright.async_api import async_playwright

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            handle_sigint=False, handle_sigterm=False, handle_sighup=False
        )
        try:
            page = await browser.new_page()
            failed = []
            page.on("requestfailed", lambda request: failed.append(request.url))
            page.on("response", lambda response: _record_http_error(response, failed))
            await page.emulate_media(media="print")
            if notebook:
                # As nbconvert's webpdf exporter does: settle 100 ms on each
                # side of the load, and print at Playwright's default page
                # size (Letter) with the template's @page margins.
                await page.wait_for_timeout(100)
                await page.goto(url, wait_until="networkidle")
                await page.wait_for_timeout(100)
            else:
                await page.goto(url, wait_until="networkidle")
            await page.evaluate(_UNLINK_LOCAL_FILES_JS, notebook)
            pdf = await page.pdf(print_background=True, prefer_css_page_size=not notebook)
            web = [u for u in dict.fromkeys(failed) if u.startswith(("http://", "https://"))]
            return Rendered(pdf, web)
        finally:
            await browser.close()


def _record_http_error(response, failed: list) -> None:
    if response.status >= 400:
        failed.append(response.url)


def _new_event_loop() -> asyncio.AbstractEventLoop:
    # Playwright drives Chromium through subprocess pipes, which on Windows need
    # the Proactor loop; make it explicit in case something set another policy.
    return asyncio.ProactorEventLoop() if os.name == "nt" else asyncio.new_event_loop()


def _run_in_fresh_loop(coro):
    loop = _new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _print_html(html: str, notebook: bool = False) -> Rendered:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        page_path = Path(tmp) / "page.html"
        page_path.write_bytes(html.encode("utf-8"))  # bytes: no newline translation
        # A private thread with its own event loop, so this also works when the
        # calling thread already runs one (inside Jupyter, for example). The
        # with-block joins the thread; nothing outlives the call.
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(_run_in_fresh_loop, _render_pdf(page_path.as_uri(), notebook)).result()


def html_to_pdf(html: str) -> bytes:
    return _print_html(html).pdf


def render(path: Path) -> Rendered:
    return _print_html(file_to_html(path), notebook=kind_for(path) == "Notebook")


def file_to_pdf_bytes(path: Path) -> bytes:
    return render(path).pdf
