from __future__ import annotations

import os
import shutil
import stat
from collections.abc import Callable
from pathlib import Path

import pytest
from mlt_stubs import AUDIO_PRODUCER, AV_PRODUCER

FIXTURES = Path(__file__).parent / "fixtures"
CAT = shutil.which("cat")  # resolved before fixtures replace PATH


@pytest.fixture(autouse=True)
def _scoped_filesystem(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Since 0.2 the server only reaches the videos directory by default (#60). Tests work in
    tmp_path and read the repository fixtures, so those are the allowed roots unless a test
    sets its own policy."""
    monkeypatch.setenv("KDENLIVE_MCP_ALLOWED_ROOTS", f"{tmp_path}{os.pathsep}{FIXTURES.resolve()}")
    # Never reach the developer's real session bus (a Kdenlive they have open, #62).
    monkeypatch.setenv("DBUS_SESSION_BUS_ADDRESS", f"unix:path={tmp_path}/no-bus")
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "no-runtime"))


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
    return {
        "PATH": str(tmp_path / "bin"),
        "HOME": os.environ.get("HOME", str(tmp_path)),
        "KDENLIVE_MCP_ALLOWED_ROOTS": os.environ["KDENLIVE_MCP_ALLOWED_ROOTS"],
    }


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


@pytest.fixture
def stub_melt(tmp_path, make_stub, stub_env):
    """A melt stub writing canned MLT XML; records its arguments."""
    profiles = tmp_path / "profiles"
    profiles.mkdir()
    shutil.copy(FIXTURES / "profiles" / "atsc_720p_2997", profiles / "atsc_720p_2997")
    shutil.copy(FIXTURES / "profiles" / "atsc_1080p_25", profiles / "atsc_1080p_25")
    canned_av = tmp_path / "av.mlt"
    canned_av.write_text(AV_PRODUCER)
    canned_audio = tmp_path / "audio.mlt"
    canned_audio.write_text(AUDIO_PRODUCER)
    args_file = tmp_path / "melt.args"
    make_stub(
        "melt",
        f'''printf "%s\\n" "$@" > "{args_file}"
for last; do :; done
case "$4" in *.wav) src="{canned_audio}";; *) src="{canned_av}";; esac
{CAT} "$src" > "${{last#xml:}}"''',
    )
    env = dict(stub_env, MLT_PROFILES_PATH=str(profiles))
    saved = dict(os.environ)
    os.environ.update(env)
    yield env, args_file
    os.environ.clear()
    os.environ.update(saved)
