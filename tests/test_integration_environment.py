"""Smoke test against the real machine; skipped when Kdenlive is not installed."""

from __future__ import annotations

import shutil

import pytest

from mcp_kdenlive.environment import ToolStatus, diagnose

pytestmark = pytest.mark.integration


@pytest.mark.skipif(shutil.which("kdenlive") is None, reason="Kdenlive is not installed on PATH")
def test_real_environment_detects_kdenlive():
    report = diagnose()
    kdenlive = next(t for t in report.tools if t.name == "kdenlive")
    assert kdenlive.status is ToolStatus.AVAILABLE, kdenlive.detail
    assert kdenlive.version
