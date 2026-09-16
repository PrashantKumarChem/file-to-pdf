# Changelog

Notable changes to File to PDF, newest first. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/).

Each release adds its section by hand (CONTRIBUTING.md, "Releases"). The 0.3.0
section was written by release-please, which the project used then.

## [0.3.0](https://github.com/PrashantKumarChem/file-to-pdf/compare/v0.2.0...v0.3.0) (2026-09-16)


### Features

* build the app with Python 3.14 and print with Chromium 151 ([09cdafd](https://github.com/PrashantKumarChem/file-to-pdf/commit/09cdafd597a0923f54a4b9f231efdb41b326be1c))

## [0.2.0] - 2026-09-14

### Changed

- The app is named File to PDF. PDFs now go to `%USERPROFILE%\File to PDF`
  (was `Notebook PDFs`) and the log to `%LOCALAPPDATA%\File to PDF` (was
  `NotebookToPDF`). From source, the code is the `topdf` package:
  `python -m topdf` runs the command line and `python -m topdf.gui` the
  window. (#7)
- Notebooks are exported with nbconvert 7.17.1, which draws mermaid diagrams
  with mermaid 11.10.0 instead of 10.7.0, so they come out larger. (#14)

### Added

- `topdf --version`. (#7)
- `LICENSE.txt` and `THIRD-PARTY-NOTICES.txt` in the app folder, with the
  licenses of the app and of everything it bundles. (#13)

### Fixed

- An app folder whose path is 125 to 169 characters long now fails with the
  explanation that the path is too long, instead of a bare "spawn ... ENOENT".
  (#15)

### Removed

- Playwright's ffmpeg is no longer bundled; the app never records video. (#13)

### Security

- nbconvert 7.17.1 and Pygments 2.20.0, for GHSA-xm59-rqc7-hhvf,
  GHSA-7jqv-fw35-gmx9, GHSA-4c99-qj7h-p3vg and GHSA-5239-wwwm-4pmq. (#14)

## [0.1.0] - 2026-09-13

First packaged release: a Windows app that needs no Python install, with a
window and a command line. It prints Jupyter notebooks, and JSON, Markdown,
code and text files 1:1. Files converted together share one browser. PDFs go
to `%USERPROFILE%\Notebook PDFs` and the log to `%LOCALAPPDATA%\NotebookToPDF`.

[0.2.0]: https://github.com/PrashantKumarChem/file-to-pdf/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/PrashantKumarChem/file-to-pdf/releases/tag/v0.1.0
