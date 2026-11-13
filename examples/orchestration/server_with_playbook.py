"""Bench only: start kdenlive-mcp with the playbook (arms/playbook.md) as its MCP instructions.

The `instructions` arm of bench.py tests whether the method reaches the client through the
server itself, which every MCP client loads, instead of a client-specific skill.
"""

import re
import sys
from pathlib import Path

from mcp_kdenlive import cli, server

body = (Path(__file__).resolve().parent / "arms" / "playbook.md").read_text(encoding="utf-8")
body = re.sub(r"\A---\n.*?\n---\n", "", body, flags=re.S).strip()
server.INSTRUCTIONS = server.INSTRUCTIONS + "\n\n" + body
if len(server.INSTRUCTIONS) > 4096:  # Claude Code truncates longer instructions
    sys.exit(f"instructions too long: {len(server.INSTRUCTIONS)}")
sys.exit(cli.main(sys.argv[1:]))
