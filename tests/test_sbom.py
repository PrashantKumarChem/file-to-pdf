"""ci/sbom.py: the lock's SBOM, kept to installed packages, plus what the zip bundles besides them."""

import json

import pytest

from ci import sbom

LOCK_SBOM = {
    "bomFormat": "CycloneDX",
    "specVersion": "1.5",
    "metadata": {"component": {"bom-ref": "file-to-pdf-1@0.2.0", "name": "file-to-pdf", "version": "0.2.0"}},
    "components": [
        {"type": "library", "bom-ref": "customtkinter-1@6.0.0", "name": "customtkinter", "version": "6.0.0"},
        {"type": "library", "bom-ref": "ipython-2@9.17.1", "name": "ipython", "version": "9.17.1"},
        {"type": "library", "bom-ref": "pexpect-3@4.9.0", "name": "pexpect", "version": "4.9.0"},
        {"type": "library", "bom-ref": "ptyprocess-4@0.7.0", "name": "ptyprocess", "version": "0.7.0"},
        {"type": "library", "bom-ref": "typing-extensions-5@4.16.0", "name": "typing-extensions", "version": "4.16.0"},
    ],
    "dependencies": [
        {"ref": "file-to-pdf-1@0.2.0", "dependsOn": ["customtkinter-1@6.0.0", "ipython-2@9.17.1"]},
        {"ref": "ipython-2@9.17.1", "dependsOn": ["pexpect-3@4.9.0", "typing-extensions-5@4.16.0"]},
        {"ref": "pexpect-3@4.9.0", "dependsOn": ["ptyprocess-4@0.7.0"]},
    ],
}

BUNDLED = sbom.bundled_components(python="3.14.7", tk="9.0.4", pyinstaller="6.22.3", chromium="151.0.7922.34")


def test_packages_not_installed_are_left_out_and_bundled_ones_added():
    installed = {"customtkinter", "ipython", "typing-extensions"}
    result, dropped = sbom.build_sbom(LOCK_SBOM, installed, BUNDLED)
    assert dropped == ["pexpect", "ptyprocess"]
    assert [entry["name"] for entry in result["components"]] == [
        "customtkinter",
        "ipython",
        "typing-extensions",
        "cpython",
        "tcl-tk",
        "pyinstaller-bootloader",
        "chromium-headless-shell",
    ]
    assert result["dependencies"] == [
        {"ref": "file-to-pdf-1@0.2.0", "dependsOn": ["customtkinter-1@6.0.0", "ipython-2@9.17.1"]},
        {"ref": "ipython-2@9.17.1", "dependsOn": ["typing-extensions-5@4.16.0"]},
    ]
    assert result["metadata"] == LOCK_SBOM["metadata"]
    # The input is left as it was.
    assert len(LOCK_SBOM["components"]) == 5


@pytest.mark.parametrize("name, installed", [("Typing_Extensions", "typing-extensions"), ("Markdown", "markdown")])
def test_names_are_compared_normalized(name, installed):
    assert sbom.normalized(name) == installed


def test_bundled_components_carry_versions_and_purls():
    by_name = {entry["name"]: entry for entry in BUNDLED}
    assert by_name["cpython"]["purl"] == "pkg:generic/cpython@3.14.7"
    assert by_name["tcl-tk"]["version"] == "9.0.4"
    assert by_name["pyinstaller-bootloader"]["purl"] == "pkg:pypi/pyinstaller@6.22.3"
    assert by_name["chromium-headless-shell"]["bom-ref"] == "chromium-headless-shell@151.0.7922.34"
    assert all(entry["properties"][0]["name"] == sbom.SOURCE for entry in BUNDLED)


def test_main_writes_the_file(tmp_path, monkeypatch, capsys):
    (tmp_path / "lock.json").write_text(json.dumps(LOCK_SBOM), encoding="utf-8")
    monkeypatch.setattr(sbom, "measured_components", lambda: BUNDLED)
    monkeypatch.setattr(sbom, "installed_names", lambda: {"customtkinter", "ipython", "typing-extensions"})
    assert sbom.main(["sbom.py", str(tmp_path / "lock.json"), str(tmp_path / "app.cdx.json")]) == 0
    written = (tmp_path / "app.cdx.json").read_bytes()
    assert b"\r" not in written
    assert len(json.loads(written)["components"]) == 7
    out = capsys.readouterr().out
    assert "Added: cpython 3.14.7, tcl-tk 9.0.4" in out
    assert "Left out, not installed in the build environment: pexpect, ptyprocess." in out


def test_main_fails_without_its_input(tmp_path, capsys):
    assert sbom.main(["sbom.py", str(tmp_path / "missing.json"), str(tmp_path / "out.json")]) == 1
    assert "Can't write the bill of materials" in capsys.readouterr().err
    assert sbom.main(["sbom.py"]) == 1


# measured_components needs the build environment (PyInstaller and Chromium's
# headless shell inside Playwright), which the tests don't install; the
# Windows app workflow's build job runs it for every build, and fails when a
# version can't be measured.
