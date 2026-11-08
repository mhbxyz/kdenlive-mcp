"""Local-first MCP server for Kdenlive projects."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("mcp-kdenlive")
except PackageNotFoundError:  # running from a source tree without installation
    __version__ = "0+unknown"

__all__ = ["__version__"]
