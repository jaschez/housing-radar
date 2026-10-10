"""Lets the tests import the modules in radar/ by name (`import detect`), the way the
scripts do, and points every test at a throwaway workspace."""
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT / "radar"))


@pytest.fixture(scope="session")
def cfg():
    return yaml.safe_load((ROOT / "tests" / "config.yaml").read_text(encoding="utf-8"))


@pytest.fixture(autouse=True)
def _workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("RADAR_WORKSPACE", str(tmp_path / "workspace"))
