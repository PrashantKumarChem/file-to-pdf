"""Routing, text decoding and HTML building. No browser needed."""

import html as html_lib
import re
from pathlib import Path

import pytest

import converters


@pytest.mark.parametrize(
    "name, kind",
    [
        ("data.json", "JSON"),
        ("notes.md", "Markdown"),
        ("notes.MARKDOWN", "Markdown"),
        ("script.py", "Code"),
        ("SCRIPT.PY", "Code"),
        ("paper.tex", "Code"),
        ("Dockerfile", "Code"),
        ("run.txt", "Text"),
        ("run.log", "Text"),
        ("mystery.zzz", "Text"),
    ],
)
def test_kind_for(name, kind):
    assert converters.kind_for(Path(name)) == kind


def test_is_recognized_flags_only_unknown_types():
    assert converters.is_recognized(Path("run.log"))
    assert converters.is_recognized(Path("script.py"))
    assert not converters.is_recognized(Path("mystery.zzz"))


def test_dialog_patterns_are_simple_sorted_globs():
    patterns = converters.dialog_patterns()
    assert patterns == sorted(set(patterns))
    assert {"*.json", "*.md", "*.py", "*.log", "*.csv"} <= set(patterns)
    assert all(p.startswith("*.") and "[" not in p for p in patterns)


@pytest.mark.parametrize(
    "raw",
    [
        "Überprüfung µ\n".encode("utf-8"),
        b"\xef\xbb\xbf" + "Überprüfung µ\n".encode("utf-8"),
        "Überprüfung µ\n".encode("utf-16"),
        "Überprüfung µ\n".encode("utf-32"),
    ],
    ids=["utf-8", "utf-8-bom", "utf-16", "utf-32"],
)
def test_read_text_decodes_unicode_encodings(tmp_path, raw):
    f = tmp_path / "t.txt"
    f.write_bytes(raw)
    assert converters.read_text(f) == "Überprüfung µ\n"


def test_read_text_falls_back_to_cp1252(tmp_path):
    f = tmp_path / "legacy.txt"
    f.write_bytes("café “quoted”".encode("cp1252"))
    assert converters.read_text(f) == "café “quoted”"


def test_read_text_normalizes_line_endings(tmp_path):
    f = tmp_path / "t.txt"
    f.write_bytes(b"a\r\nb\rc\n")
    assert converters.read_text(f) == "a\nb\nc\n"


def test_read_text_rejects_binary(tmp_path):
    f = tmp_path / "blob.bin"
    f.write_bytes(bytes(range(256)))
    with pytest.raises(converters.UnsupportedFileError, match="binary"):
        converters.read_text(f)


def printed_text(html: str, preformatted: bool = True) -> str:
    """The text a reader sees: markup stripped, entities decoded.

    For preformatted output (JSON, code, text) that is the <pre> content,
    minus the single newline HTML drops right after the <pre> tag.
    """
    body = html.split("<body>", 1)[1].rsplit("</body>", 1)[0]
    if preformatted:
        body = re.search(r"<pre>(.*)</pre>", body, re.S).group(1).removeprefix("\n")
    return html_lib.unescape(re.sub(r"<[^>]+>", "", body))


@pytest.mark.parametrize("name", ["data.json", "script.py", "run.log"])
def test_output_is_the_file_and_nothing_else(tmp_path, name):
    folder = tmp_path / "private folder"
    folder.mkdir()
    source = '\n{"mass": 1.0, "exp": 1.50e3, "neg": -0.00,\n    "odd":   [1,2 ,3], "html": "<b>&amp;"}\n\n'
    f = folder / name
    f.write_text(source, encoding="utf-8")
    html = converters.file_to_html(f)
    # Exactly the file's text, including number spelling, spacing and
    # leading/trailing blank lines...
    assert printed_text(html) == source
    # ...and nothing printed around it: no heading, file name or folder.
    body = html.split("<body>", 1)[1].rsplit("</body>", 1)[0]
    assert re.sub(r"<[^>]+>", "", re.sub(r"<pre>.*</pre>", "", body, flags=re.S)).strip() == ""
    assert f'<base href="{folder.as_uri()}/">' in html


def test_json_tokens_get_the_paper_palette(tmp_path):
    f = tmp_path / "x.json"
    f.write_text('{"key": "null inside", "flag": true, "n": null, "v": 2.50}', encoding="utf-8")
    html = converters.file_to_html(f)
    css = converters._PYGMENTS_CSS
    assert '<span class="nt">&quot;key&quot;</span>' in html
    assert '<span class="s2">&quot;null inside&quot;</span>' in html
    assert '<span class="kc">true</span>' in html and '<span class="kc">null</span>' in html
    css = css.lower()
    assert ".source .nt { color: #9b2158; font-weight: bold }" in css
    assert ".source .s2 { color: #0f7d33 }" in css
    assert ".source .kc { color: #7a3fc4 }" in css
    assert ".source .mf {" not in css  # numbers stay in the body text color


def test_markup_in_json_is_escaped(tmp_path):
    f = tmp_path / "x.json"
    f.write_text('{"<b>": "<script>alert(1)</script>"}', encoding="utf-8")
    html = converters.file_to_html(f)
    assert "<script>alert" not in html
    assert "&lt;script&gt;" in html


def test_markdown_is_rendered_without_additions(tmp_path):
    f = tmp_path / "notes.md"
    f.write_text("# Title\n\nSome *text*.\n\n```python\nx = 1\n```\n", encoding="utf-8")
    html = converters.file_to_html(f)
    assert printed_text(html, preformatted=False).split() == ["Title", "Some", "text.", "x", "=", "1"]
    # Fenced code uses the same palette as code files.
    assert '<div class="source"><pre>' in html


def test_code_highlighting_handles_crlf_sources(tmp_path):
    f = tmp_path / "s.py"
    f.write_bytes(b"def f():\r\n    return 1\r\n")
    html = converters.file_to_html(f)
    assert "\r" not in html
    assert 'class="k">def</span>' in html
