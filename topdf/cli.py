"""
File to PDF from the command line: the window's conversion, for folders,
scripts and batches.

    topdf analysis.ipynb data\\*.json
    topdf "C:\\Projects\\results" --recursive --next-to-source
    topdf notes.md --out C:\\PDFs

From source, run `python -m topdf` with the same arguments. Output, names and
the log are exactly as in the window (see README). Each file prints one status
line. Exit code: 0 when everything converted, 1 when a file failed or a path
matched nothing, 2 when there was nothing to convert.
"""

import argparse
import glob
import os
import sys
from pathlib import Path

from topdf import __version__, converters, pipeline


def _hidden(path: Path, root: Path) -> bool:
    return any(part.startswith(".") for part in path.relative_to(root).parts)


def _prints(path: Path) -> bool:
    """Whether a file found in a folder is one to print.

    PDFs never are: that includes this tool's own output when a folder is
    converted --next-to-source a second time. Anything else prints if its type
    is recognized or it reads as text, since coordinate files, spectra exports
    and input decks often have extensions no highlighter knows (.xyz, .jdx).
    Images, spreadsheets and other binary files are left out.
    """
    if path.suffix.lower() == ".pdf":
        return False
    return converters.is_recognized(path) or converters.looks_like_text(path)


def collect(args: list[str], recursive: bool = False) -> tuple[list[Path], list[str], list[Path]]:
    """Expand command-line paths into the files to convert.

    Returns the files, the arguments that matched nothing, and the files
    folders held that won't be printed. A file named directly always converts
    (binary ones then fail with a clear status, as in the window). A folder
    contributes the files that print, skipping hidden entries such as
    .ipynb_checkpoints. Wildcards are expanded here because cmd and PowerShell
    pass them through unexpanded.
    """
    files, missing, skipped = [], [], []
    for arg in args:
        path = Path(arg)
        if path.is_file():
            files.append(path)
        elif path.is_dir():
            found = path.rglob("*") if recursive else path.glob("*")
            for p in sorted(p for p in found if p.is_file() and not _hidden(p, path)):
                (files if _prints(p) else skipped).append(p)
        elif glob.has_magic(arg) and (matches := sorted(
            Path(m) for m in glob.glob(arg, recursive=recursive) if Path(m).is_file()
        )):
            files.extend(matches)
        else:
            missing.append(arg)
    unique = {os.path.normcase(os.path.abspath(p)): p for p in reversed(files)}
    return list(reversed(unique.values())), missing, skipped


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="topdf",
        description="Convert notebooks, JSON, Markdown, code and text files to PDF, 1:1.",
    )
    parser.add_argument("paths", nargs="+", help="files, folders or wildcards")
    where = parser.add_mutually_exclusive_group()
    where.add_argument("-o", "--out", type=Path, metavar="FOLDER",
                       help=f"save the PDFs here (default: {pipeline.DEFAULT_OUTPUT_DIR})")
    where.add_argument("--next-to-source", action="store_true",
                       help="save each PDF next to the file it came from")
    parser.add_argument("-r", "--recursive", action="store_true",
                        help="also convert files in subfolders of the folders given")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    args = parser.parse_args(argv)
    # Paths and statuses can hold any character; never let printing them fail.
    sys.stdout.reconfigure(errors="replace")
    sys.stderr.reconfigure(errors="replace")

    files, missing, skipped = collect(args.paths, args.recursive)
    for arg in missing:
        print(f"Not found: {arg}", file=sys.stderr)
    if skipped:
        shown = ", ".join(p.name for p in skipped[:5]) + (", ..." if len(skipped) > 5 else "")
        noun = "file" if len(skipped) == 1 else "files"
        print(f"Skipped {len(skipped)} {noun} (PDF or not text): {shown}", file=sys.stderr)
    if not files:
        print("Nothing to convert.", file=sys.stderr)
        return 2

    failed = 0
    with converters.batch():  # one browser for the whole run
        for n, path in enumerate(files, 1):
            output_dir = path.parent if args.next_to_source else (args.out or pipeline.DEFAULT_OUTPUT_DIR)
            result = pipeline.convert_any(path, output_dir)
            failed += not result.ok
            print(f"[{n}/{len(files)}] {path.name}: {result.status}", flush=True)
    summary = f"{len(files) - failed} of {len(files)} converted"
    print(summary + (f", {failed} failed (details in {pipeline.LOG_PATH})" if failed else ""))
    return 1 if failed or missing else 0


if __name__ == "__main__":
    sys.exit(main())
