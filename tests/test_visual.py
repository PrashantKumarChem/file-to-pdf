"""ci/visual.py: fingerprints, their comparison and validation, and the difference images. No browser needed."""

import copy
import json

import pytest

pymupdf = pytest.importorskip("pymupdf")  # PyMuPDF, from the dev dependency group

from ci import visual  # noqa: E402

SHA_A, SHA_B = "a" * 64, "b" * 64


def page(pixels=SHA_A, lines=SHA_A, line_count=3, links=()):
    return {"pixels": pixels, "lines": lines, "line_count": line_count, "links": list(links)}


def fingerprints(**files):
    return {"format": visual.FORMAT, "dpi": visual.DPI, "files": files}


def entry(*pages, title="t"):
    return {"title": title, "pages": list(pages) or [page()]}


def write_pdf(path, lines, link=None):
    doc = pymupdf.open()
    pdf_page = doc.new_page(width=612, height=792)
    for n, text in enumerate(lines):
        pdf_page.insert_text((72, 72 + 14 * n), text)
    if link:
        pdf_page.insert_link({"kind": pymupdf.LINK_URI, "from": pymupdf.Rect(72, 60, 200, 76), "uri": link})
    doc.set_metadata({"title": path.stem})
    doc.save(path)
    doc.close()
    return path


def test_the_same_pdf_gives_the_same_fingerprint(tmp_path):
    a = write_pdf(tmp_path / "a.pdf", ["first line", "second line"], link="https://example.com/")
    first, second = visual.fingerprint_pdf(a), visual.fingerprint_pdf(a)
    assert first == second
    assert first["title"] == "a"
    assert first["pages"][0]["line_count"] == 2
    assert first["pages"][0]["links"] == ["uri https://example.com/"]
    assert visual.validate(fingerprints(a=first)) == []


def test_moved_text_differs_in_lines_and_pixels(tmp_path):
    a = visual.fingerprint_pdf(write_pdf(tmp_path / "x.pdf", ["first line", "second line"]))
    b = visual.fingerprint_pdf(write_pdf(tmp_path / "x.pdf", ["first line", "", "second line"]))
    result = visual.compare_file(a, b)
    assert result["verdict"] == "structure"
    assert result["details"] == ["page 1: text lines, pixels"]
    assert result["pages"] == [1]


@pytest.mark.parametrize(
    "change, verdict, details",
    [
        (lambda e: None, "same", []),
        (lambda e: e["pages"][0].update(pixels=SHA_B), "pixels", ["page 1: pixels"]),
        (lambda e: e["pages"][0].update(lines=SHA_B), "structure", ["page 1: text lines"]),
        (lambda e: e["pages"][0].update(links=["uri https://example.com/"]), "structure", ["page 1: links"]),
        (lambda e: e.update(title="other"), "structure", ["title 't' became 'other'"]),
        (lambda e: e["pages"].append(page()), "structure", ["2 pages became 3"]),
    ],
)
def test_compare_file_verdicts(change, verdict, details):
    stored = entry(page(), page())
    actual = copy.deepcopy(stored)
    change(actual)
    result = visual.compare_file(stored, actual)
    assert (result["verdict"], result["details"]) == (verdict, details)


def test_a_new_page_is_listed_as_differing():
    assert visual.compare_file(entry(page()), entry(page(), page()))["pages"] == [2]


def test_new_and_missing_files_differ():
    stored = fingerprints(kept=entry(), removed=entry())
    actual = fingerprints(kept=entry(), added=entry())
    comparison = visual.compare(stored, actual, before=None)
    assert comparison["differs"]
    assert {name: r["verdict"] for name, r in comparison["files"].items()} == {
        "added": "new",
        "kept": "same",
        "removed": "missing",
    }


@pytest.mark.parametrize(
    "before_pixels, after_pixels, expected",
    [
        (SHA_A, SHA_B, "this change"),
        (SHA_B, SHA_B, "not this change"),
        (SHA_B, "c" * 64, "this change, and the base branch"),
    ],
)
def test_cause_uses_the_base_branch_rendering(before_pixels, after_pixels, expected):
    stored = fingerprints(f=entry(page(pixels=SHA_A)))
    before = fingerprints(f=entry(page(pixels=before_pixels)))
    actual = fingerprints(f=entry(page(pixels=after_pixels)))
    assert visual.compare(stored, actual, before)["files"]["f"]["cause"].startswith(expected)


def test_cause_of_new_and_unrendered_files():
    rendered = fingerprints(f=entry())
    assert (
        visual.compare(fingerprints(), rendered, rendered)["files"]["f"]["cause"] == "no fingerprint stored for it yet"
    )
    assert visual.compare(rendered, fingerprints(), rendered)["files"]["f"]["cause"] == "this change didn't render it"
    assert visual.compare(rendered, fingerprints(f=entry(title="x")), fingerprints())["files"]["f"]["cause"] == (
        "the base branch didn't render it"
    )


def test_summary_names_the_differences_and_the_accept_workflow():
    comparison = visual.compare(fingerprints(f=entry()), fingerprints(f=entry(page(pixels=SHA_B))), before=None)
    text = visual.summary(comparison, failed={"f": ["https://cdn.example/x.js"]}, pr="42")
    assert "**1 of 1 files differ" in text
    assert "| `f` | pixels only | page 1: pixels | - |" in text
    assert "pull request number 42" in text
    assert "https://cdn.example/x.js" in text
    assert "All 1 files match" in visual.summary(
        visual.compare(fingerprints(f=entry()), fingerprints(f=entry()), None), {}, None
    )


@pytest.mark.parametrize(
    "mutate, problem",
    [
        (lambda d: d.update(extra=1), "exactly: format, dpi, files"),
        (lambda d: d.update(dpi=72), "dpi must be 96"),
        (lambda d: d.update(files={}), "non-empty"),
        (lambda d: d["files"].update({"../escape": entry()}), "not a plain file name"),
        (lambda d: d["files"]["f"]["pages"][0].update(pixels="not-a-hash"), "SHA-256"),
        (lambda d: d["files"]["f"]["pages"][0].update(line_count=True), "whole number"),
        (lambda d: d["files"]["f"]["pages"][0].update(links=[1]), "list of strings"),
        (lambda d: d["files"]["f"]["pages"][0].update(script="x"), "must have exactly"),
        (lambda d: d["files"]["f"].update(pages=[]), "non-empty list"),
    ],
)
def test_validate_rejects_malformed_fingerprints(mutate, problem):
    data = fingerprints(f=entry())
    assert visual.validate(data) == []
    mutate(data)
    assert any(problem in p for p in visual.validate(data)), visual.validate(data)


def test_images_of_a_differing_page(tmp_path):
    before_dir, after_dir, out = tmp_path / "before", tmp_path / "after", tmp_path / "images"
    before_dir.mkdir()
    after_dir.mkdir()
    write_pdf(before_dir / "notes.md.pdf", ["same", "old words"])
    write_pdf(after_dir / "notes.md.pdf", ["same", "new words"])
    comparison = visual.compare(
        fingerprints(**{"notes.md": visual.fingerprint_pdf(before_dir / "notes.md.pdf")}),
        fingerprints(**{"notes.md": visual.fingerprint_pdf(after_dir / "notes.md.pdf")}),
        before=None,
    )
    assert visual.images(before_dir, after_dir, comparison, out) == 0
    names = sorted(p.name for p in (out / "notes.md").iterdir())
    assert names == ["page-1-after.png", "page-1-before.png", "page-1-difference.png"]
    difference = pymupdf.Pixmap(str(out / "notes.md" / "page-1-difference.png"))
    red = bytes((0xD0, 0x10, 0x10))
    samples = difference.samples
    assert red in bytes(samples)


def test_validate_command_exit_codes(tmp_path):
    good, bad = tmp_path / "good.json", tmp_path / "bad.json"
    good.write_text(json.dumps(fingerprints(f=entry())), encoding="utf-8")
    bad.write_text(json.dumps({"format": 1}), encoding="utf-8")
    assert visual.main(["validate", str(good)]) == 0
    assert visual.main(["validate", str(bad)]) == 1
