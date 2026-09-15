# Contributing

Bug reports, fixes and improvements are welcome.

## Reporting a bug

Open an issue with the bug report form. The most useful reports include the
version (`topdf --version`, or the release you downloaded), whether you run
the Windows app or run from source, and a small file that shows the problem if
you can share one.

The log (`%LOCALAPPDATA%\File to PDF\conversion_log.txt`) records the full
path of every converted file, so remove anything private before pasting from
it.

For security problems, don't open an issue: see [SECURITY.md](SECURITY.md).

## Suggesting a change

For anything larger than a small fix, open an issue first so the approach can
be agreed before you spend time on it. One principle to keep in mind: the PDF
holds the file's content and nothing else, so changes that add headings, file
names or extra sections to the output won't be merged.

## Setting up

1. Install [uv](https://docs.astral.sh/uv/getting-started/installation/), then
   clone the repository and install Python, the dependencies (including the
   test tools) and Playwright's Chromium:

   ```
   git clone https://github.com/PrashantKumarChem/file-to-pdf.git
   cd file-to-pdf
   uv sync
   uv run python -m playwright install chromium
   ```

   uv installs the Python version in `.python-version` and the exact package
   versions in `uv.lock`.

2. Run the window with `uv run python -m topdf.gui` or the command line with
   `uv run python -m topdf`.

## Tests

```
uv run python -m pytest                 # everything, about 30 seconds
uv run python -m pytest -m "not slow"   # skip Chromium and nbconvert rendering
uv run coverage run -m pytest           # everything, measuring coverage of topdf/
uv run coverage report                  # fails below the floor
```

The GUI tests open a hidden window, so run them from a desktop session. Add or
update tests for any change in behavior.

The coverage floor is `fail_under` in `pyproject.toml`. CI fails when the
total drops below it, and when a pull request lowers it: raise it when your
tests add coverage, never lower it.

## Style and types

```
uv run ruff check .          # lint
uv run ruff format .         # format (--check to only report)
uv run pyright               # types
```

Their versions are locked with the other development tools and their
settings are in `pyproject.toml`.

## Dependencies

Dependencies are declared in `pyproject.toml` and locked, with every package
they pull in, in `uv.lock`. To add or change one, edit `pyproject.toml` (or run
`uv add`), run `uv lock`, and commit both files.

Every pull request, and a weekly run, also checks the supply chain: that
`uv.lock` matches `pyproject.toml`, that no locked package has a known
vulnerability, and that the workflows avoid unsafe patterns. To run the same
checks locally:

```
uv lock --check
uv export --frozen --all-groups --format requirements.txt -o locked-requirements.txt
uv run --group audit pip-audit -r locked-requirements.txt --require-hashes --disable-pip
uv run --group audit zizmor .github/workflows
```

## How the PDFs look

Every pull request renders the synthetic files in `tests/visual/corpus` on
Windows and compares the PDFs with `tests/visual/fingerprints.json`: page
count, pixels of each page, text line positions, links and title. Nothing in
the corpus comes from real work; add files there only if you wrote them for
this purpose.

When a PDF differs, the "visual" check fails. Its summary says which pages
differ and whether the pull request or something else (the runner, PyMuPDF)
caused it, and the run's `visual-comparison` artifact holds before, after and
difference images of those pages. If the new look is intended, a maintainer
runs **Actions → Accept new rendering → Run workflow** with the pull request's
number. That commits the fingerprints the run measured, and the checks run
again on that commit.

The fingerprints are made on the Windows runner. A local rendering can differ,
so don't commit fingerprints made on your own machine. To look locally:

```
uv run python ci/visual.py render tests/visual/corpus out
uv run python ci/visual.py fingerprint out out/fingerprints.json
uv run python ci/visual.py compare tests/visual/fingerprints.json out/fingerprints.json
```

## The Windows app, end to end

Pull requests that change `topdf/`, `pyproject.toml`, `uv.lock`,
`.python-version`, `packaging/` or the app workflow build the Windows app, zip
it, unzip the zip into a fresh folder and test that folder
(`packaging/e2e`): `topdf.exe` on every file type in the corpus, without
network, from a long folder path, into a folder that refuses writes, with
non-English and same-named files, and the window clicked through by script.
The "app" check passes when all of that passes, or when nothing the app is
built from changed.

The window test moves the real mouse, and the no-network test adds Windows
Firewall rules, so run the suite on a machine nobody is using, elevated:

```
uv run python -m pytest packaging/e2e --app dist\FileToPDF
```

## Pull requests

- Branch from `main` and keep each pull request to one change.
- Describe what changes for someone using the tool, and how you tested it.
- Update the README when behavior it describes changes.
- Every pull request runs the tests on Windows with the Python in
  `.python-version` and the packages in `uv.lock`, and the style, type,
  supply-chain and visual checks. Changes to what the app is built from also
  build the Windows app and test it end to end.
- Pull requests are merged with a merge commit, so each commit should stand
  on its own with a message that says what it does.

If you used AI tools, say so in the pull request description, as described in
[AI_USAGE.md](AI_USAGE.md).

By contributing, you agree that your contributions are licensed under the
[MIT License](LICENSE).
