"""Static packaging invariants checked on every test run (artifact checks live in scripts/)."""

from __future__ import annotations

import tomllib
from pathlib import Path

from mcp_kdenlive import __version__

ROOT = Path(__file__).resolve().parents[1]
SERVER_NAME = "io.github.mhbxyz/kdenlive-mcp"


def _pyproject() -> dict:
    return tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))


def test_distribution_metadata():
    project = _pyproject()["project"]
    assert project["name"] == "mcp-kdenlive"
    assert project["license"] == "MIT"
    assert project["requires-python"] == ">=3.11"
    assert project["scripts"] == {
        "kdenlive-mcp": "mcp_kdenlive.cli:main",
        "mcp-kdenlive": "mcp_kdenlive.cli:main",
    }


def test_installed_version_matches_pyproject():
    assert __version__ == _pyproject()["project"]["version"]


def test_readme_carries_mcp_registry_marker():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert f"<!-- mcp-name: {SERVER_NAME} -->" in readme


def test_server_json_matches_package_and_readme():
    import json

    server = json.loads((ROOT / "server.json").read_text(encoding="utf-8"))
    assert server["name"] == SERVER_NAME
    assert server["$schema"].startswith("https://static.modelcontextprotocol.io/schemas/")
    (package,) = server["packages"]
    assert (package["registryType"], package["identifier"]) == ("pypi", "mcp-kdenlive")
    assert package["transport"] == {"type": "stdio"}
    assert package["runtimeHint"] == "uvx"  # works because the package also installs `mcp-kdenlive`
    assert server["version"] == package["version"] == _pyproject()["project"]["version"]
    documented = {
        "KDENLIVE_MCP_ALLOWED_ROOTS",
        "KDENLIVE_MCP_KDENLIVE",
        "KDENLIVE_MCP_MELT",
        "KDENLIVE_MCP_FFPROBE",
    }
    assert {v["name"] for v in package["environmentVariables"]} == documented
    assert not any(v.get("isSecret") for v in package["environmentVariables"])
