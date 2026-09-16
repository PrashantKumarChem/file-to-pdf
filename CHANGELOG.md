# Changelog

Notable changes to File to PDF, newest first. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/).

Each release adds its section by hand (CONTRIBUTING.md, "Releases").

1.0.0 is the only release kept on GitHub. The 0.x releases and their tags
were removed to save space; their notes stay below.

## [1.0.0] - 2026-09-16

### Added

- The drop area lists the file types that convert, and says how code and
  text files print.
- Folders can be dropped on the window: the files in them that print are
  added, as `topdf FOLDER` does. Drops land anywhere on the window, and the
  drop area lights up while dragging.
- A Help window (Help button or F1), tooltips on every control, and a summary
  of what the tool does while the list is empty.
- Double-click a file to open its PDF; right-click for Open PDF, Show in
  folder, Copy details and Remove from list. Delete, Ctrl+C, Ctrl+A and
  Ctrl+O work.
- A line under the list counts finished and failed files, with a progress
  bar while converting. The window shows the version. (#53)

### Changed

- The list's text scales with Windows display scaling, each status is short
  and marked (✓, ⚠, ✕), and a "Saved as" column shows where each PDF went.
  "Open full log" is now "Open log". (#53)

### Fixed

- "Clear list" and removing a row skip files that haven't started, instead
  of converting them out of sight. (#53)

## 0.3.0 - 2026-09-16

### Changed

- The app is built with Python 3.14 and prints with Chromium 151.

## 0.2.0 - 2026-09-14

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

## 0.1.0 - 2026-09-13

First packaged release: a Windows app that needs no Python install, with a
window and a command line. It prints Jupyter notebooks, and JSON, Markdown,
code and text files 1:1. Files converted together share one browser. PDFs go
to `%USERPROFILE%\Notebook PDFs` and the log to `%LOCALAPPDATA%\NotebookToPDF`.

[1.0.0]: https://github.com/PrashantKumarChem/file-to-pdf/releases/tag/v1.0.0
