"""The four demos of examples/demos (#92), played by their deterministic MCP client scripts."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

DEMOS = Path(__file__).resolve().parents[2] / "examples" / "demos"
NAMES = ["travel_vlog", "interview", "rescue_project", "vertical_short"]


def load(name: str):  # type: ignore[no-untyped-def]
    """Import a demo's run_demo.py; each demo has its own `make_media`, so the module cache and
    sys.path are restored afterwards."""
    folder = DEMOS / name
    saved_path = list(sys.path)
    sys.modules.pop("make_media", None)
    try:
        spec = importlib.util.spec_from_file_location(f"demo_{name}", folder / "run_demo.py")
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)  # inserts its folder and examples/demos into sys.path
        return module
    finally:
        sys.path[:] = saved_path


@pytest.mark.timeout(900)
@pytest.mark.parametrize("name", NAMES)
def test_demo(name, tmp_path, media_tools):
    out, failures = load(name).run(tmp_path / name)
    assert out.is_file()
    assert failures == []


def test_every_demo_folder_has_its_files():
    for name in NAMES:
        files = {p.name for p in (DEMOS / name).iterdir()}
        assert {"README.md", "prompt.md", "make_media.py", "run_demo.py"} <= files, name
