"""
Format adapters and the shared HTML -> PDF engine.

Architecture: one PDF engine, many small "render to HTML" adapters.
Every file, notebooks included, is turned into an HTML string here and printed
to PDF by one Chromium/Playwright engine. Notebooks are exported by nbconvert's
HTML exporter with the bundled topdf-notebook template and printed the way
nbconvert's webpdf exporter prints them, so their PDFs are the ones
`nbconvert --to webpdf` produced, without a second Python process.

Public API:
    render(path) -> Rendered                # routes by kind_for(path): PDF + failed web requests
    file_to_pdf_bytes(path) -> bytes
    html_to_pdf(html_str) -> bytes
    batch()                                 # context manager: prints inside share one browser
    kind_for(path) -> "Notebook" | "JSON" | "Markdown" | "Code" | "Text"
    is_recognized(path) -> bool             # False: printed as plain text by default
    dialog_patterns() -> list[str]          # glob patterns for a file picker
    UnsupportedFileError                    # raised for binary input
"""

import asyncio
import codecs
import contextlib
import html as html_lib
import os
import re
import tempfile
import threading
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


_BOMS = (
    (codecs.BOM_UTF32_LE, "utf-32"), (codecs.BOM_UTF32_BE, "utf-32"),
    (codecs.BOM_UTF8, "utf-8-sig"),
    (codecs.BOM_UTF16_LE, "utf-16"), (codecs.BOM_UTF16_BE, "utf-16"),
)
_BINARY_PROBE_BYTES = 8192


def _is_binary(data: bytes) -> bool:
    # UTF-16 and UTF-32 text is full of NUL bytes but starts with a BOM;
    # without one, a NUL near the start means binary.
    return not data.startswith(tuple(bom for bom, _ in _BOMS)) and b"\x00" in data[:_BINARY_PROBE_BYTES]


def looks_like_text(path: Path) -> bool:
    """Whether read_text would accept the file, judged from its first bytes."""
    try:
        with open(path, "rb") as f:
            return not _is_binary(f.read(_BINARY_PROBE_BYTES))
    except OSError:
        return True  # unreadable: let the conversion report why


def read_text(path: Path) -> str:
    """Decode a text file whatever its encoding; raise for binary files.

    A byte-order mark wins (UTF-8, UTF-16 and UTF-32 all carry one when
    Windows tools write them). Otherwise UTF-8, falling back to cp1252 for
    older Windows text. NUL bytes without a BOM mean binary. Line endings are
    normalized to \\n, as text-mode reading would.
    """
    data = path.read_bytes()
    if _is_binary(data):
        raise UnsupportedFileError(f"{path.name} is a binary file, not text")
    for bom, encoding in _BOMS:
        if data.startswith(bom):
            text = data.decode(encoding, errors="replace")
            break
    else:
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            text = data.decode("cp1252", errors="replace")
    return text.replace("\r\n", "\n").replace("\r", "\n")


class PaperStyle(Style):
    """The syntax palette for every highlighted file: JSON, code, Markdown code.

    Deliberately muted: JSON data files can run to many pages of long string
    values, and editor-bright colors on those tire the eye. Keys (and markup
    tags) bold maroon, strings green, true/false/null and other keywords
    purple, comments grey; numbers and punctuation stay in the body text color.
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
# The @page rule is the only page setup: the print call in _render_page passes
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
# Notebooks: nbconvert's HTML exporter with the template bundled in this package
# ---------------------------------------------------------------------------
TEMPLATE_BASE_DIR = Path(__file__).parent / "templates"
TEMPLATE_NAME = "topdf-notebook"


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


async def _render_page(browser, url: str, notebook: bool) -> Rendered:
    context = await browser.new_context()
    try:
        page = await context.new_page()
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
            # The page shell runs no scripts, so once "load" has fired
            # (every image included, remote ones too) nothing else will
            # arrive; networkidle only added 500 ms of quiet per file.
            await page.goto(url, wait_until="load")
        await page.evaluate(_UNLINK_LOCAL_FILES_JS, notebook)
        pdf = await page.pdf(print_background=True, prefer_css_page_size=not notebook)
        web = [u for u in dict.fromkeys(failed) if u.startswith(("http://", "https://"))]
        return Rendered(pdf, web)
    finally:
        with contextlib.suppress(Exception):  # the browser may already be gone
            await context.close()


def _record_http_error(response, failed: list) -> None:
    if response.status >= 400:
        failed.append(response.url)


def _new_event_loop() -> asyncio.AbstractEventLoop:
    # Playwright drives Chromium through subprocess pipes, which on Windows need
    # the Proactor loop; make it explicit in case something set another policy.
    return asyncio.ProactorEventLoop() if os.name == "nt" else asyncio.new_event_loop()


class _Renderer:
    """A Playwright driver and Chromium browser living on one private thread.

    Playwright objects belong to the event loop that created them, while
    prints come from the GUI worker, the command line, or a thread already
    running its own loop (Jupyter), so every print is handed to this thread's
    loop. The driver and browser start with the first print.
    """

    def __init__(self):
        self._loop = _new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, name="pdf-renderer", daemon=True)
        self._thread.start()
        # Made on the calling thread but only awaited on the loop's; fine since
        # Python 3.10, where a Lock binds to a loop at first use, not creation.
        self._starting = asyncio.Lock()
        self._playwright = None
        self._browser = None

    def print(self, url: str, notebook: bool) -> Rendered:
        return asyncio.run_coroutine_threadsafe(self._print(url, notebook), self._loop).result()

    def close(self) -> None:
        try:
            asyncio.run_coroutine_threadsafe(self._shutdown(), self._loop).result()
        finally:
            self._loop.call_soon_threadsafe(self._loop.stop)
            self._thread.join()
            self._loop.close()

    async def _print(self, url: str, notebook: bool) -> Rendered:
        try:
            return await _render_page(await self._started_browser(), url, notebook)
        except Exception as error:  # noqa: BLE001 - re-raised unless the browser died
            if not self._lost_browser(error):
                raise
        # The browser or its driver died (crashed or killed) partway through a
        # batch: start both again and give this file one more try.
        await self._stop()
        return await _render_page(await self._started_browser(), url, notebook)

    def _lost_browser(self, error: Exception) -> bool:
        from playwright._impl._errors import TargetClosedError  # not exported publicly

        # A dead driver leaves is_connected() True and raises a plain
        # Exception, so its message is the only sign.
        return (
            isinstance(error, TargetClosedError)
            or "Connection closed" in str(error)
            or (self._browser is not None and not self._browser.is_connected())
        )

    async def _started_browser(self):
        async with self._starting:
            if self._playwright is None:
                from playwright.async_api import async_playwright

                self._playwright = await async_playwright().start()
            if self._browser is None:
                chromium = self._playwright.chromium
                # Windows won't start a program whose path is 260 characters or
                # longer, and Chromium then fails with a bare "spawn ... ENOENT".
                # The packaged app keeps Chromium about 135 characters deep, so
                # an app folder with a long path runs into this.
                if os.name == "nt" and len(chromium.executable_path) >= 260:
                    raise RuntimeError(
                        f"Chromium's path is {len(chromium.executable_path)} characters, past "
                        "Windows' 260-character limit for starting programs; move the app "
                        "to a folder with a shorter path"
                    )
                self._browser = await chromium.launch(
                    handle_sigint=False, handle_sigterm=False, handle_sighup=False
                )
            return self._browser

    async def _stop(self) -> None:
        browser, playwright = self._browser, self._playwright
        self._browser = self._playwright = None
        for stop in (browser and browser.close, playwright and playwright.stop):
            if stop:
                with contextlib.suppress(Exception):  # already dead: nothing to stop
                    await asyncio.wait_for(stop(), 10)

    async def _shutdown(self) -> None:
        await self._stop()
        tasks = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


_batch_lock = threading.Lock()
_batch_depth = 0
_batch_renderer: _Renderer | None = None


@contextlib.contextmanager
def batch():
    """Print every file inside the block with one shared browser.

    Starting the Playwright driver and Chromium costs about half a second per
    print. Inside a batch they start with the first print and stop when the
    outermost batch ends; outside one, each print starts and stops its own,
    so nothing outlives the call. Enter the batch on the thread that prints.
    """
    global _batch_depth, _batch_renderer
    with _batch_lock:
        _batch_depth += 1
    try:
        yield
    finally:
        with _batch_lock:
            _batch_depth -= 1
            renderer = _batch_renderer if _batch_depth == 0 else None
            if renderer is not None:
                _batch_renderer = None
        if renderer is not None:
            renderer.close()


def _print_html(html: str, notebook: bool = False) -> Rendered:
    global _batch_renderer
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        page_path = Path(tmp) / "page.html"
        page_path.write_bytes(html.encode("utf-8"))  # bytes: no newline translation
        with _batch_lock:
            shared = _batch_depth > 0
            if shared and _batch_renderer is None:
                _batch_renderer = _Renderer()
            renderer = _batch_renderer if shared else _Renderer()
        try:
            return renderer.print(page_path.as_uri(), notebook)
        finally:
            if not shared:
                renderer.close()


def html_to_pdf(html: str) -> bytes:
    return _print_html(html).pdf


def render(path: Path) -> Rendered:
    return _print_html(file_to_html(path), notebook=kind_for(path) == "Notebook")


def file_to_pdf_bytes(path: Path) -> bytes:
    return render(path).pdf
