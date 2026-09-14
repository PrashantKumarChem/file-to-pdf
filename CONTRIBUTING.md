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
```

The GUI tests open a hidden window, so run them from a desktop session. Add or
update tests for any change in behavior.

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

## Pull requests

- Branch from `main` and keep each pull request to one change.
- Describe what changes for someone using the tool, and how you tested it.
- Update the README when behavior it describes changes.
- Every pull request runs the tests on Windows with the Python in
  `.python-version` and the packages in `uv.lock`. Changes to `topdf/`,
  `pyproject.toml`, `uv.lock`, `.python-version` or `packaging/` also build and
  smoke-test the Windows app.
- Pull requests are merged with a merge commit, so each commit should stand
  on its own with a message that says what it does.

If you used AI tools, say so in the pull request description, as described in
[AI_USAGE.md](AI_USAGE.md).

By contributing, you agree that your contributions are licensed under the
[MIT License](LICENSE).
