"""End-to-end level: the real server over stdio, real MLT/FFmpeg, objective checks of renders.

Helpers live in examples/demos/demolib.py so the demos and these tests share them.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

DEMOS = Path(__file__).resolve().parents[2] / "examples" / "demos"
if not DEMOS.is_dir():  # e.g. tests run from an sdist, which does not ship the demos
    collect_ignore_glob = ["test_*.py"]
else:
    sys.path.insert(0, str(DEMOS))


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        if Path(str(item.fspath)).parent == Path(__file__).parent:
            item.add_marker(pytest.mark.e2e)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def media_tools() -> None:
    import demolib

    if not demolib.have("melt", "ffprobe", "ffmpeg"):
        pytest.skip("e2e needs melt, ffprobe and ffmpeg")
