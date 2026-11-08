from __future__ import annotations

import os
import stat
from collections.abc import Callable
from pathlib import Path

import pytest


@pytest.fixture
def make_stub(tmp_path: Path) -> Callable[[str, str], Path]:
    """Create an executable POSIX shell stub named `name` in a private bin directory."""
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)

    def _make(name: str, body: str) -> Path:
        path = bindir / name
        path.write_text("#!/bin/sh\n" + body + "\n", encoding="utf-8")
        path.chmod(path.stat().st_mode | stat.S_IXUSR)
        return path

    return _make


@pytest.fixture
def stub_env(tmp_path: Path) -> dict[str, str]:
    """A minimal environment whose PATH only contains the stub directory."""
    (tmp_path / "bin").mkdir(exist_ok=True)
    return {"PATH": str(tmp_path / "bin"), "HOME": os.environ.get("HOME", str(tmp_path))}


@pytest.fixture
def all_tools_stubbed(make_stub, stub_env) -> dict[str, str]:  # type: ignore[no-untyped-def]
    make_stub("kdenlive", 'echo "kdenlive 26.08.2"')
    make_stub("melt", 'echo "melt 7.42.0"; echo "Copyright (C) 2002-2026 Meltytech, LLC"')
    make_stub("ffprobe", 'echo "ffprobe version n9.0.2 Copyright (c) 2007-2026"')
    make_stub("ffmpeg", 'echo "ffmpeg version n9.0.2 Copyright (c) 2000-2026"')
    return stub_env


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"
