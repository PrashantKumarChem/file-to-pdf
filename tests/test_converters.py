"""Routing, text decoding and HTML building. No browser needed."""

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


def test_file_to_html_keeps_source_path_out_of_visible_text(tmp_path):
    folder = tmp_path / "private folder"
    folder.mkdir()
    f = folder / "data.json"
    f.write_text('{"a": 1}', encoding="utf-8")
    html = converters.file_to_html(f)
    assert "<p class='subtle'>JSON</p>" in html
    body = html.split("</head>", 1)[1]
    assert "private folder" not in body
    assert f'<base href="{folder.as_uri()}/">' in html


def test_json_values_are_escaped(tmp_path):
    f = tmp_path / "x.json"
    f.write_text('{"<b>": "<script>alert(1)</script>"}', encoding="utf-8")
    html = converters.file_to_html(f)
    assert "<script>alert" not in html
    assert "&lt;script&gt;" in html


def test_code_highlighting_handles_crlf_sources(tmp_path):
    f = tmp_path / "s.py"
    f.write_bytes(b"def f():\r\n    return 1\r\n")
    html = converters.file_to_html(f)
    assert "\r" not in html
    assert 'class="k">def</span>' in html
