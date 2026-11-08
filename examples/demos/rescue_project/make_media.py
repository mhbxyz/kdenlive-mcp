"""The project to rescue: a Kdenlive 26.08.2 project with crossfades whose media folder was renamed.

Uses the repository's fixture `tests/fixtures/kd26/kd26-mixes.kdenlive` (three clips crossfaded
by Kdenlive itself, synthetic CC0 media; see tests/fixtures/PROVENANCE.md). Its media are copied
to `footage/` instead of `media/`, so the project opens with missing media.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

FIXTURES = Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "kd26"


def make(work: Path) -> dict[str, Path]:
    work.mkdir(parents=True, exist_ok=True)
    project = work / "rescue.kdenlive"
    shutil.copy(FIXTURES / "kd26-mixes.kdenlive", project)
    footage = work / "footage"
    footage.mkdir(exist_ok=True)
    for media in (FIXTURES / "media").iterdir():
        shutil.copy(media, footage / media.name)  # "media/" was renamed "footage/"
    return {"project": project, "footage": footage}


if __name__ == "__main__":
    for path in make(Path(sys.argv[1] if len(sys.argv) > 1 else ".").resolve()).values():
        print(path)
