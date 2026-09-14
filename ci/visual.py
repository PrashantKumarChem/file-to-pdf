"""Visual comparison: render a fixed corpus and compare the PDFs with stored fingerprints.

    python ci/visual.py render CORPUS OUT_DIR [--code DIR]
    python ci/visual.py fingerprint PDF_DIR OUT.json
    python ci/visual.py compare STORED.json ACTUAL.json [--before BEFORE.json]
                                [--failed FAILED.json] [--json OUT.json] [--summary OUT.md] [--pr N]
    python ci/visual.py images BEFORE_DIR AFTER_DIR COMPARISON.json OUT_DIR
    python ci/visual.py validate FINGERPRINTS.json

render prints every file directly in CORPUS to OUT_DIR/<name>.pdf with topdf's
converters, one browser for all. The topdf used is this repository's, or the
one in --code DIR, which is how the base branch's rendering is made with this
script. Web requests that failed go to OUT_DIR/failed-requests.json.

A fingerprint records each PDF's title and, page by page, a SHA-256 of its
pixels at 96 dpi, a SHA-256 of its text lines with their positions to 0.1 pt,
the number of lines and the link targets. compare exits 1 when a file differs,
is new or is missing, and says whether each difference is in the pixels only
or in the layout, text or links; given the base branch's fingerprints
(--before), it also says whether the change or something outside it (the
runner, PyMuPDF) caused it. images writes the before, after and difference
PNGs of the differing pages. validate checks a fingerprints file's shape, and
needs only the standard library.
"""

import argparse
import hashlib
import json
import re
import sys
from io import TextIOWrapper
from pathlib import Path
from typing import Any, cast

FORMAT = 1
DPI = 96
ROOT = Path(__file__).resolve().parents[1]
MAX_IMAGE_PAGES = 3
_SHA256 = re.compile(r"[0-9a-f]{64}")
_PAGE_KEYS = {"pixels", "lines", "line_count", "links"}


# ---------------------------------------------------------------------------
# render
# ---------------------------------------------------------------------------
def render(corpus: Path, out: Path, code: Path) -> int:
    sys.path.insert(0, str(code))
    from topdf import converters

    print(f"rendering with {Path(converters.__file__).parent}")
    out.mkdir(parents=True, exist_ok=True)
    failed: dict[str, list[str]] = {}
    with converters.batch():
        for source in sorted(p for p in corpus.iterdir() if p.is_file()):
            rendered = converters.render(source)
            (out / f"{source.name}.pdf").write_bytes(rendered.pdf)
            if rendered.failed_requests:
                failed[source.name] = rendered.failed_requests
            note = f", {len(rendered.failed_requests)} web requests failed" if rendered.failed_requests else ""
            print(f"  {source.name}{note}")
    (out / "failed-requests.json").write_text(json.dumps(failed, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    return 0


# ---------------------------------------------------------------------------
# fingerprint
# ---------------------------------------------------------------------------
def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _link_target(link: dict) -> str:
    import pymupdf

    if link.get("kind") == pymupdf.LINK_URI:
        return f"uri {link.get('uri')}"
    if link.get("kind") == pymupdf.LINK_GOTO:
        return f"page {link.get('page', -1) + 1}"
    if link.get("kind") == pymupdf.LINK_NAMED:  # how Chromium writes a jump to an #anchor
        return f"named {link.get('nameddest')} on page {link.get('page', -1) + 1}"
    return f"kind {link.get('kind')}"


def fingerprint_pdf(path: Path) -> dict:
    import pymupdf

    pages = []
    with pymupdf.open(path) as doc:
        title = (doc.metadata or {}).get("title") or ""
        for page in doc:
            text: Any = page.get_text("dict")
            lines = [
                [[round(value, 1) for value in line["bbox"]], "".join(span["text"] for span in line["spans"])]
                for block in text["blocks"]
                for line in block.get("lines", [])
            ]
            pages.append(
                {
                    "pixels": _sha256(page.get_pixmap(dpi=DPI).samples),
                    "lines": _sha256(json.dumps(lines, ensure_ascii=False).encode("utf-8")),
                    "line_count": len(lines),
                    "links": sorted(_link_target(link) for link in page.get_links()),
                }
            )
    return {"title": title, "pages": pages}


def fingerprint_dir(pdf_dir: Path) -> dict:
    files = {pdf.name.removesuffix(".pdf"): fingerprint_pdf(pdf) for pdf in sorted(pdf_dir.glob("*.pdf"))}
    return {"format": FORMAT, "dpi": DPI, "files": files}


def dump(fingerprints: dict) -> str:
    return json.dumps(fingerprints, indent=1, sort_keys=True, ensure_ascii=False) + "\n"


# ---------------------------------------------------------------------------
# validate
# ---------------------------------------------------------------------------
def validate(data: object) -> list[str]:
    """Problems with a fingerprints file's shape; empty when it is well formed."""
    if not isinstance(data, dict) or set(data) != {"format", "dpi", "files"}:
        return ["the top level must be an object with exactly: format, dpi, files"]
    problems = []
    if data["format"] != FORMAT:
        problems.append(f"format must be {FORMAT}")
    if data["dpi"] != DPI:
        problems.append(f"dpi must be {DPI}")
    files = data["files"]
    if not isinstance(files, dict) or not files:
        return [*problems, "files must be a non-empty object"]
    for name, entry in files.items():
        where = f"files[{name!r}]"
        if not name or name.startswith(".") or "/" in name or "\\" in name:
            problems.append(f"{where}: not a plain file name")
        if not isinstance(entry, dict) or set(entry) != {"title", "pages"}:
            problems.append(f"{where}: must have exactly: title, pages")
            continue
        if not isinstance(entry["title"], str):
            problems.append(f"{where}.title: must be a string")
        pages = entry["pages"]
        if not isinstance(pages, list) or not pages:
            problems.append(f"{where}.pages: must be a non-empty list")
            continue
        for number, page in enumerate(pages, 1):
            at = f"{where} page {number}"
            if not isinstance(page, dict) or set(page) != _PAGE_KEYS:
                problems.append(f"{at}: must have exactly: {', '.join(sorted(_PAGE_KEYS))}")
                continue
            for key in ("pixels", "lines"):
                if not isinstance(page[key], str) or not _SHA256.fullmatch(page[key]):
                    problems.append(f"{at}.{key}: must be a SHA-256 in lower-case hex")
            count = page["line_count"]
            if not isinstance(count, int) or isinstance(count, bool) or count < 0:
                problems.append(f"{at}.line_count: must be a whole number")
            if not isinstance(page["links"], list) or not all(isinstance(link, str) for link in page["links"]):
                problems.append(f"{at}.links: must be a list of strings")
    return problems


def load(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    problems = validate(data)
    if problems:
        raise SystemExit(f"{path} is not a valid fingerprints file:\n  " + "\n  ".join(problems))
    return data


# ---------------------------------------------------------------------------
# compare
# ---------------------------------------------------------------------------
VERDICT_WORDS = {
    "same": "same",
    "pixels": "pixels only",
    "structure": "layout, text or links",
    "new": "new file",
    "missing": "missing",
}


def compare_file(expected: dict | None, actual: dict | None) -> dict:
    """How actual differs from expected: a verdict, readable details, and the differing page numbers."""
    if expected is None and actual is None:
        return {"verdict": "same", "details": [], "pages": []}
    if expected is None:
        return {"verdict": "new", "details": ["no stored fingerprint"], "pages": []}
    if actual is None:
        return {"verdict": "missing", "details": ["not rendered"], "pages": []}
    details: list[str] = []
    pages: list[int] = []
    structural = False
    if expected["title"] != actual["title"]:
        details.append(f"title {expected['title']!r} became {actual['title']!r}")
        structural = True
    before_pages, after_pages = expected["pages"], actual["pages"]
    if len(before_pages) != len(after_pages):
        details.append(f"{len(before_pages)} pages became {len(after_pages)}")
        structural = True
    # strict=False: a changed page count was reported above; compare the pages both have.
    for number, (before, after) in enumerate(zip(before_pages, after_pages, strict=False), 1):
        kinds = []
        if before["links"] != after["links"]:
            kinds.append("links")
        if before["lines"] != after["lines"] or before["line_count"] != after["line_count"]:
            kinds.append("text lines")
        structural = structural or bool(kinds)
        if before["pixels"] != after["pixels"]:
            kinds.append("pixels")
        if kinds:
            pages.append(number)
            details.append(f"page {number}: {', '.join(kinds)}")
    pages.extend(range(min(len(before_pages), len(after_pages)) + 1, max(len(before_pages), len(after_pages)) + 1))
    verdict = "same" if not details else "structure" if structural else "pixels"
    return {"verdict": verdict, "details": details, "pages": pages}


def cause(stored: dict | None, before: dict | None, after: dict | None) -> str:
    """Why a file's rendering differs from the stored fingerprint, judged by the base branch's rendering."""
    if stored is None:
        return "no fingerprint stored for it yet"
    if after is None:
        return "this change didn't render it"
    if before is None:
        return "the base branch didn't render it"
    base_matches_stored = compare_file(stored, before)["verdict"] == "same"
    change_matches_base = compare_file(before, after)["verdict"] == "same"
    if base_matches_stored:
        return "this change"
    if change_matches_base:
        return "not this change: the base branch renders it the same way (the runner or PyMuPDF changed)"
    return "this change, and the base branch no longer matches the stored fingerprint either"


def compare(stored: dict, actual: dict, before: dict | None) -> dict:
    names = sorted(set(stored["files"]) | set(actual["files"]))
    files = {}
    for name in names:
        result = compare_file(stored["files"].get(name), actual["files"].get(name))
        if result["verdict"] != "same" and before is not None:
            result["cause"] = cause(stored["files"].get(name), before["files"].get(name), actual["files"].get(name))
        files[name] = result
    return {"differs": any(r["verdict"] != "same" for r in files.values()), "files": files}


def summary(comparison: dict, failed: dict, pr: str | None) -> str:
    files = comparison["files"]
    differing = {name: r for name, r in files.items() if r["verdict"] != "same"}
    out = ["## Visual comparison", ""]
    if not differing:
        out.append(f"All {len(files)} files match the stored fingerprints.")
    else:
        out.append(f"**{len(differing)} of {len(files)} files differ from the stored fingerprints.**")
        out += ["", "| File | What differs | Where | Cause |", "| --- | --- | --- | --- |"]
        for name, r in differing.items():
            where = "; ".join(r["details"]) or "-"
            out.append(f"| `{name}` | {VERDICT_WORDS[r['verdict']]} | {where} | {r.get('cause', '-')} |")
        out += [
            "",
            "The before, after and difference images of the differing pages (up to "
            f"{MAX_IMAGE_PAGES} per file), both renderings' PDFs and the new fingerprints are in this "
            "run's `visual-comparison` artifact.",
            "",
            "If the new rendering is right, accept it: Actions, Accept new rendering, Run workflow, "
            f"with pull request number {pr or '(this pull request)'}.",
        ]
    if failed:
        out += ["", "Web requests that failed while rendering, which can change how a page looks:"]
        out += [f"- `{name}`: {', '.join(urls)}" for name, urls in sorted(failed.items())]
    return "\n".join(out) + "\n"


# ---------------------------------------------------------------------------
# images
# ---------------------------------------------------------------------------
def _page_pixmap(pdf: Path, number: int):
    import pymupdf

    if not pdf.is_file():
        return None
    with pymupdf.open(pdf) as doc:
        return doc[number - 1].get_pixmap(dpi=DPI) if number <= len(doc) else None


def difference_pixmap(before, after):
    """After, faded to grey, with every pixel that differs from before in red."""
    import pymupdf

    a, b, n = before.samples, after.samples, after.n
    out = bytearray(len(b))
    for i in range(0, len(b), n):
        if a[i : i + n] != b[i : i + n]:
            out[i : i + 3] = b"\xd0\x10\x10"
        else:
            grey = 255 - (255 - b[i]) // 4
            out[i : i + 3] = bytes((grey, grey, grey))
    return pymupdf.Pixmap(pymupdf.csRGB, after.width, after.height, bytes(out), False)


def images(before_dir: Path, after_dir: Path, comparison: dict, out: Path) -> int:
    written = 0
    for name, result in comparison["files"].items():
        if result["verdict"] == "same":
            continue
        for number in result["pages"][:MAX_IMAGE_PAGES]:
            folder = out / name
            folder.mkdir(parents=True, exist_ok=True)
            before = _page_pixmap(before_dir / f"{name}.pdf", number)
            after = _page_pixmap(after_dir / f"{name}.pdf", number)
            for label, pixmap in (("before", before), ("after", after)):
                if pixmap is not None:
                    pixmap.save(folder / f"page-{number}-{label}.png")
                    written += 1
            if (
                before is not None
                and after is not None
                and (before.width, before.height) == (after.width, after.height)
            ):
                difference_pixmap(before, after).save(folder / f"page-{number}-difference.png")
                written += 1
    print(f"wrote {written} images to {out}")
    return 0


# ---------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="visual.py", description=(__doc__ or "").split("\n\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)
    p = commands.add_parser("render")
    p.add_argument("corpus", type=Path)
    p.add_argument("out", type=Path)
    p.add_argument("--code", type=Path, default=ROOT)
    p = commands.add_parser("fingerprint")
    p.add_argument("pdf_dir", type=Path)
    p.add_argument("out", type=Path)
    p = commands.add_parser("compare")
    p.add_argument("stored", type=Path)
    p.add_argument("actual", type=Path)
    p.add_argument("--before", type=Path)
    p.add_argument("--failed", type=Path)
    p.add_argument("--json", type=Path)
    p.add_argument("--summary", type=Path)
    p.add_argument("--pr")
    p = commands.add_parser("images")
    p.add_argument("before_dir", type=Path)
    p.add_argument("after_dir", type=Path)
    p.add_argument("comparison", type=Path)
    p.add_argument("out", type=Path)
    p = commands.add_parser("validate")
    p.add_argument("fingerprints", type=Path)
    args = parser.parse_args(argv)
    # File names can hold any character; never let printing them fail.
    cast(TextIOWrapper, sys.stdout).reconfigure(errors="replace")

    if args.command == "render":
        return render(args.corpus, args.out, args.code)
    if args.command == "fingerprint":
        fingerprints = fingerprint_dir(args.pdf_dir)
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(dump(fingerprints), encoding="utf-8")
        print(f"fingerprinted {len(fingerprints['files'])} PDFs into {args.out}")
        return 0
    if args.command == "compare":
        stored = load(args.stored) if args.stored.is_file() else {"format": FORMAT, "dpi": DPI, "files": {}}
        comparison = compare(stored, load(args.actual), load(args.before) if args.before else None)
        failed = json.loads(args.failed.read_text(encoding="utf-8")) if args.failed and args.failed.is_file() else {}
        text = summary(comparison, failed, args.pr)
        print(text)
        if args.json:
            args.json.write_text(json.dumps(comparison, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
        if args.summary:
            with open(args.summary, "a", encoding="utf-8") as f:
                f.write(text)
        return 1 if comparison["differs"] else 0
    if args.command == "images":
        comparison = json.loads(args.comparison.read_text(encoding="utf-8"))
        return images(args.before_dir, args.after_dir, comparison, args.out)
    problems = validate(json.loads(args.fingerprints.read_text(encoding="utf-8")))
    for problem in problems:
        print(problem, file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
