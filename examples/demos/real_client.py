"""Play a demo's prompt with a real MCP client (Claude Code, `claude -p`) and summarise the run.

Not part of CI. Usage:

    python examples/demos/real_client.py DEMO WORKDIR [--model MODEL]

The client only gets the `kdenlive-mcp` server (`--strict-mcp-config`, only its tools allowed),
started from this checkout with KDENLIVE_MCP_ALLOWED_ROOTS limited to WORKDIR. The media are
generated first (the demo's make_media.py). The summary (client version, model, tool calls,
errors, cost, final answer, and objective facts read back from the project and the render) is
printed as JSON for RESULTS.md.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import anyio

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import demolib as dl  # noqa: E402 - after the path setup

SERVER = Path(sys.executable).with_name("kdenlive-mcp")


def make_media(demo: str, work: Path) -> None:
    folder = HERE / demo
    sys.path.insert(0, str(folder))
    spec = importlib.util.spec_from_file_location(f"media_{demo}", folder / "make_media.py")
    if spec is None or spec.loader is None:
        raise SystemExit(f"no make_media.py in {folder}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.make(work)


def prompt(demo: str, work: Path, part: int = 0) -> str:
    """The `part`-th blockquote of the demo's prompt.md, with WORKDIR filled in."""
    blocks: list[list[str]] = [[]]
    for line in (HERE / demo / "prompt.md").read_text(encoding="utf-8").splitlines():
        if line.startswith(">"):
            blocks[-1].append(line[2:] if line.startswith("> ") else "")
        elif blocks[-1]:
            blocks.append([])
    quoted = [b for b in blocks if b]
    return "\n".join(quoted[part]).replace("WORKDIR", str(work)).strip()


def claude(text: str, work: Path, model: str | None, resume: str | None = None) -> list[dict]:
    config = work / ".mcp-kdenlive.json"
    env = {"KDENLIVE_MCP_ALLOWED_ROOTS": str(work)}
    for key in ("DISPLAY", "WAYLAND_DISPLAY", "XAUTHORITY"):
        if os.environ.get(key):
            env[key] = os.environ[key]
    config.write_text(
        json.dumps({"mcpServers": {"kdenlive": {"command": str(SERVER), "args": [], "env": env}}})
    )
    args = [
        shutil.which("claude") or "claude",
        "-p",
        text,
        "--mcp-config",
        str(config),
        "--strict-mcp-config",
        "--allowedTools",
        "mcp__kdenlive",
        "--disallowedTools",
        "Bash,Write,Edit,NotebookEdit,WebFetch,WebSearch",
        "--output-format",
        "stream-json",
        "--verbose",
    ]
    if model:
        args += ["--model", model]
    if resume:
        args += ["--resume", resume]
    done = subprocess.run(  # noqa: S603 - argument list, no shell
        args, cwd=work, capture_output=True, text=True, timeout=3600, check=False
    )
    stream = work / f"claude-{'resume' if resume else 'first'}.jsonl"
    stream.write_text(done.stdout, encoding="utf-8")  # kept for the record
    return [json.loads(line) for line in done.stdout.splitlines() if line.startswith("{")]


def summarise(events: list[dict]) -> dict[str, Any]:
    init = next((e for e in events if e.get("type") == "system" and e.get("subtype") == "init"), {})
    result = next((e for e in reversed(events) if e.get("type") == "result"), {})
    calls, errors = [], []
    pending: dict[str, str] = {}
    for event in events:
        message = event.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        for part in content if isinstance(content, list) else []:
            if not isinstance(part, dict):
                continue
            if part.get("type") == "tool_use":
                name = part["name"].removeprefix("mcp__kdenlive__")
                calls.append(name)
                pending[part["id"]] = name
            elif part.get("type") == "tool_result" and part.get("is_error"):
                content = part.get("content")
                text = (
                    content
                    if isinstance(content, str)
                    else " ".join(c.get("text", "") for c in content or [] if isinstance(c, dict))
                )
                errors.append(f"{pending.get(part.get('tool_use_id'), '?')}: {text[:300]}")
    return {
        "client": f"Claude Code {init.get('claude_code_version', '?')}",
        "model": init.get("model"),
        "session": result.get("session_id") or init.get("session_id"),
        "turns": result.get("num_turns"),
        "duration_s": round((result.get("duration_ms") or 0) / 1000),
        "cost_usd": result.get("total_cost_usd"),
        "tool_calls": calls,
        "errors": errors,
        "answer": result.get("result"),
    }


async def facts(work: Path) -> dict[str, Any]:
    """What is objectively in the work folder after the run."""
    out: dict[str, Any] = {}
    async with dl.connect(dl.server_env([work])) as s:
        for project in sorted(p for p in work.glob("*.kdenlive") if ".backup-" not in p.name):
            tl = await s.timeline(project)
            media = await s.call("list_project_media", path=str(project))
            markers = await s.call("list_markers", path=str(project))
            subs = await s.call("list_subtitles", path=str(project))
            out[project.name] = {
                "duration_frames": tl["duration_frames"],
                "fps": tl["frame_rate"]["fps"],
                "tracks": {
                    t["label"]: [
                        (c["start"], c["end"], c["name"], c["effects"], c.get("audio"))
                        for c in t["clips"]
                    ]
                    for t in tl["tracks"]
                    if t["clips"]
                },
                "mixes": {
                    t["label"]: [(m["start"], m["end"]) for m in t["mixes"]]
                    for t in tl["tracks"]
                    if t["mixes"]
                },
                "missing_media": [
                    m["reference"] for m in media["items"] if m["status"] == "missing"
                ],
                "guides": [(g["position"], g["comment"]) for g in markers["guides"]],
                "subtitles": [len(t["events"]) for t in subs["tracks"]],
            }
    for video in sorted(work.glob("*.mp4")):
        out[video.name] = {
            "size": dl.video_size(video),
            "duration_s": round(dl.duration(video), 2),
            "streams": dl.stream_kinds(video),
        }
    return out


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("demo")
    parser.add_argument("work", type=Path)
    parser.add_argument("--model")
    parser.add_argument("--then", action="store_true", help="send prompt.md's second part")
    parser.add_argument("--between", help="shell-free step between the prompts: 'move:A:B'")
    parser.add_argument("--summarise", action="store_true", help="re-read the saved streams only")
    opts = parser.parse_args()
    work = opts.work.resolve()
    if opts.summarise:
        runs = [
            summarise(
                [
                    json.loads(line)
                    for line in stream.read_text().splitlines()
                    if line.startswith("{")
                ]
            )
            for stream in sorted(work.glob("claude-*.jsonl"))
        ]
        print(json.dumps({"runs": runs, "facts": anyio.run(facts, work)}, indent=1, default=str))
        return
    work.mkdir(parents=True, exist_ok=True)
    make_media(opts.demo, work)
    first = summarise(claude(prompt(opts.demo, work), work, opts.model))
    runs = [first]
    if opts.between and opts.between.startswith("move:"):
        _, src, dst = opts.between.split(":")
        (work / dst).parent.mkdir(parents=True, exist_ok=True)
        shutil.move(work / src, work / dst)
    if opts.then:
        runs.append(
            summarise(
                claude(
                    opts.then.replace("WORKDIR", str(work)),
                    work,
                    opts.model,
                    resume=first["session"],
                )
            )
        )
    print(json.dumps({"runs": runs, "facts": anyio.run(facts, work)}, indent=1, default=str))


if __name__ == "__main__":
    main()
