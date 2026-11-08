"""Smoke-test an *installed* kdenlive-mcp: version output and a stdio MCP round trip.

Run it with the interpreter of the environment where the artifact was installed, from a
directory outside the source tree so the checkout is not imported by accident:
    /path/to/venv/bin/python scripts/smoke_installed.py [EXPECTED_VERSION]
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path


def main() -> None:
    expected = sys.argv[1] if len(sys.argv) > 1 else None
    exe = Path(sys.executable).parent / "kdenlive-mcp"
    if not exe.exists():
        found = shutil.which("kdenlive-mcp")
        if not found:
            raise SystemExit("FAIL: kdenlive-mcp console script not installed")
        exe = Path(found)
    out = subprocess.run([str(exe), "--version"], capture_output=True, text=True, timeout=60, check=True)
    print(out.stdout.strip())
    if expected and out.stdout.strip() != f"kdenlive-mcp {expected}":
        raise SystemExit(f"FAIL: version {out.stdout.strip()!r} != {expected!r}")

    proc = subprocess.Popen([str(exe)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True)

    def send(msg: dict) -> dict | None:
        assert proc.stdin and proc.stdout
        proc.stdin.write(json.dumps(msg) + "\n")
        proc.stdin.flush()
        if "id" not in msg:
            return None
        while True:
            line = proc.stdout.readline()
            if not line:
                raise SystemExit(f"FAIL: server closed stdout: {proc.stderr.read() if proc.stderr else ''}")
            data = json.loads(line)  # raises if stdout carries non-protocol output
            if data.get("id") == msg["id"]:
                return data

    try:
        init = send({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2025-06-18", "capabilities": {},
            "clientInfo": {"name": "smoke", "version": "0"}}})
        assert init and init["result"]["serverInfo"]["name"] == "kdenlive-mcp", init
        send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        call = send({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                     "params": {"name": "diagnose_environment", "arguments": {}}})
        assert call and call["result"]["isError"] is False, call
        ready = call["result"]["structuredContent"]["ready"]
        print(f"OK: stdio round trip, diagnose_environment ready={ready}")
    finally:
        if proc.stdin:
            proc.stdin.close()
        proc.wait(timeout=30)


if __name__ == "__main__":
    main()
