import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from topdf import pipeline


@pytest.fixture
def isolated_pipeline(tmp_path, monkeypatch):
    """Point every pipeline location at tmp_path and forget earlier PDFs."""
    locations = {
        "LOG_PATH": tmp_path / "log.txt",
        "DEFAULT_OUTPUT_DIR": tmp_path / "out",
        "FALLBACK_DIR": tmp_path / "fallback",
    }
    for name, value in locations.items():
        monkeypatch.setattr(pipeline, name, value)
    monkeypatch.setattr(pipeline, "_source_of_pdf", {})
    monkeypatch.setattr(pipeline.time, "sleep", lambda _seconds: None)
    return locations
