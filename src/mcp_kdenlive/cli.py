"""Command-line entry point: `kdenlive-mcp` starts the stdio MCP server."""

from __future__ import annotations

import argparse
import logging
import os
import sys

from mcp_kdenlive import __version__
from mcp_kdenlive.environment import (
    EnvironmentReport,
    FilesystemAccess,
    diagnose,
    filesystem_access,
)
from mcp_kdenlive.server import build_server


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="kdenlive-mcp",
        description="Local MCP server for Kdenlive (stdio transport).",
    )
    parser.add_argument("--version", action="version", version=f"kdenlive-mcp {__version__}")
    parser.add_argument(
        "--log-level",
        default="WARNING",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Diagnostics verbosity on stderr (default: WARNING).",
    )
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("serve", help="Run the MCP server over stdio (default).")
    diag = sub.add_parser("diagnose", help="Print the environment report and exit.")
    diag.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    return parser


def _scope(files: FilesystemAccess) -> str:
    if files.error:
        return f"INVALID: {files.error}"
    if files.roots is None:
        return "unrestricted (KDENLIVE_MCP_ALLOWED_ROOTS=unrestricted)"
    origin = (
        "default: videos directory" if files.source == "default" else "KDENLIVE_MCP_ALLOWED_ROOTS"
    )
    text = f"{':'.join(files.roots)} ({origin})"
    if files.missing:
        text += f"; missing: {', '.join(files.missing)}"
    return text


def _print_human(report: EnvironmentReport) -> None:
    print(
        f"kdenlive-mcp {report.server_version} on {report.platform.system} "
        f"{report.platform.release} (Python {report.platform.python})"
    )
    for tool in report.tools:
        flag = "required" if tool.required else "optional"
        line = f"  {tool.name:<9} {tool.status.value:<16} {flag:<9}"
        if tool.version:
            line += f" {tool.version}"
        if tool.path:
            line += f"  [{tool.path}]"
        print(line)
        if tool.status.value != "available" and tool.hint:
            print(f"            hint: {tool.hint}")
    print(f"  files     {_scope(report.filesystem)}")
    print("ready" if report.ready else "NOT ready: a required program is unavailable")


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    logging.basicConfig(
        level=args.log_level, stream=sys.stderr, format="%(levelname)s %(name)s: %(message)s"
    )
    if args.command == "diagnose":
        report = diagnose()
        if args.json:
            print(report.model_dump_json(indent=2))
        else:
            _print_human(report)
        return 0 if report.ready else 1
    # One line on stderr (never stdout, which carries the protocol) so client logs show the scope.
    print(
        f"kdenlive-mcp: Filesystem access: {_scope(filesystem_access(os.environ))}", file=sys.stderr
    )
    build_server().run("stdio")
    return 0
