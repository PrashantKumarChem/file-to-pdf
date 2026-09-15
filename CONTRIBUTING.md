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
vulnerability, that the workflows avoid unsafe patterns and Dependabot waits
at least 14 days before proposing a release, and that `.github/dependabot.yml`
matches Dependabot's schema. To run the same checks locally:

```
uv lock --check
uv export --frozen --all-groups --format requirements.txt -o locked-requirements.txt
uv run --group audit pip-audit -r locked-requirements.txt --require-hashes --disable-pip
uv run --group audit zizmor --config .github/zizmor.yml .github
uv run --group audit check-jsonschema --builtin-schema vendor.dependabot .github/dependabot.yml
```

### Dependency updates

Dependabot proposes updates for the Python packages in `pyproject.toml` and
`uv.lock` (every dependency group) and for the GitHub Actions the workflows
use. `.github/dependabot.yml` configures it:

- **Version updates** come once a quarter, on the first day of January, April,
  July and October, and only for releases that have been out for at least 14
  days. For each ecosystem, minor and patch updates arrive in one pull request
  and major updates in another.
- **Security updates** open as soon as GitHub raises a Dependabot alert,
  grouped per ecosystem. The schedule and the 14-day wait don't apply to them.

Dependabot titles its Python updates `deps: ...`, so merging one makes a patch
release, and its GitHub Actions updates `ci: ...`, which don't make a release
(see [Titles and commit messages](#titles-and-commit-messages)).

Review an update like any other pull request: its checks show whether the
tests, the PDFs ("visual") or the Windows app ("app") are affected. A major
update may need code changes, which is why it comes on its own.

Dependabot reads `.github/dependabot.yml` only from `main`, so pull requests
check it first: against Dependabot's schema, and for a cooldown of at least
the 14 days in `.github/zizmor.yml` (change both files together). After a
change to it merges, check that GitHub accepted it under **Insights →
Dependency graph → Dependabot**: **Recent update jobs** next to each
ecosystem's file lists the runs, and **view logs** shows any error.

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
`.python-version`, `packaging/`, `ci/sbom.py`, `ci/checksums.py` or the app
workflow build the Windows app, zip it, unzip the zip into a fresh folder and
test that folder (`packaging/e2e`): `topdf.exe` on every file type in the
corpus, without network, from a long folder path, into a folder that refuses
writes, with non-English and same-named files, and the window clicked through
by script. The "app" check passes when all of that passes, or when nothing the
app is built from changed.

The app is built once, on Windows Server 2025. Pull requests test it there;
release tags and manual runs of the workflow test the same zip on Windows
Server 2022 and 2025, and every leg must pass.

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

### Titles and commit messages

Releases are made from pull request titles. A pull request's merge commit
carries its title, and
[release-please](https://github.com/googleapis/release-please) reads those
titles on `main` to choose the next version and write the changelog. So the
title is a [conventional commit](https://www.conventionalcommits.org/en/v1.0.0/):
a type, an optional scope in parentheses, a colon, a space and what changes.

```
fix: keep spaces in file names
feat(gui): drop files on the window
```

| Type | For | Version after merging |
|---|---|---|
| `feat` | something new people can do | minor (0.2.0 to 0.3.0) |
| `fix` | a bug fix | patch (0.2.0 to 0.2.1) |
| `perf` | the same result, faster | patch |
| `deps` | a dependency update (Dependabot's Python updates) | patch |
| `revert` | undoing an earlier change | patch |
| `docs`, `style`, `refactor`, `test`, `build`, `ci`, `chore` | changes that don't reach the app | no release on its own |

Add `!` after the type or scope (`feat!: need Python 3.14`) for a change that
breaks how people use the tool. While the version is below 1.0.0, that raises
the minor version, as `feat` does.

The commits on the branch keep plain sentences that say what they do ("Check
pull request titles"), without a type. They land on `main` with the merge, and
release-please would read a conventional one as a second change and list it
twice. The "pr-title" check fails when the title isn't a conventional commit,
or when a commit on the branch starts a paragraph with a type that makes a
release, and runs again when you edit the title. Dependabot's commits are
exempt, because Dependabot gives each commit its pull request's title.

## Releases

Releases are made from `main` by
[release-please](https://github.com/googleapis/release-please) and the
Release workflow (`.github/workflows/release.yml`):

1. After each merge, release-please keeps one pull request, the Release PR
   (its branch is `release-please--branches--main`), up to date. It holds the
   next version, the changelog entry written from the titles merged since the
   last release, and that version in `.release-please-manifest.json`,
   `pyproject.toml`, `topdf/__init__.py`, `CITATION.cff` and `uv.lock`. It
   exists only once something that makes a release has merged.
2. Merging the Release PR tags its merge commit `v` and the version, and marks
   the pull request `autorelease: tagged`.
3. The tag starts the Windows app workflow. It checks the tag against every
   version file (`ci/versions.py --tag`), builds the app, tests the zip end to
   end and publishes the release.

Don't edit the Release PR by hand: release-please rewrites its branch whenever
`main` changes. To choose a version yourself, 1.0.0 for example, add
`"release-as": "1.0.0"` under `packages` → `"."` in
`release-please-config.json` in a pull request, and remove it again after
that release. `uv run python ci/versions.py` checks that the version files
agree; the tests run it on every pull request.

The Release workflow acts with a token from the project's release GitHub App,
because a tag or pull request made with the workflow's own token starts no
checks, or starts them waiting for approval. The app's client ID
(`RELEASE_APP_CLIENT_ID`, a variable) and private key
(`RELEASE_APP_PRIVATE_KEY`, a secret) belong to the `release` environment,
which only `main` may use. If tagging fails, re-run the Release workflow's run
for that merge commit.

If you used AI tools, say so in the pull request description, as described in
[AI_USAGE.md](AI_USAGE.md).

By contributing, you agree that your contributions are licensed under the
[MIT License](LICENSE).
