"""Compare ways of orchestrating a real assistant on the demos (not in CI).

Arms (the client is Claude Code, `claude -p`, with only this server; shell, file writing and web
tools are disallowed in every arm):

- `single`: one agent, the server as it is (its instructions and tool descriptions);
- `playbook`: one agent, plus a project skill describing the method (arms/playbook.md);
- `subagents`: an orchestrator that plans, delegates edits to an `editor` sub-agent and checks
  with a read-only `verifier` sub-agent (arms/editor.md, arms/verifier.md).

Each run gets a fresh work folder with freshly generated media, then is scored by score.py.

    python examples/orchestration/bench.py RUNS_DIR --demos all --arms all --reps 1 --jobs 3
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "demos"))
sys.path.insert(0, str(HERE))
import real_client as rc  # noqa: E402
import score  # noqa: E402

ARMS = ("single", "playbook", "subagents", "instructions")
DEMOS = ("travel_vlog", "interview", "rescue_project", "vertical_short")
DISALLOWED = ["Bash", "Write", "Edit", "NotebookEdit", "WebFetch", "WebSearch"]
ORCHESTRATOR = (
    "You orchestrate this video editing task. 1) Read the brief and the inputs (read-only kdenlive "
    "tools are fine), write a numbered checklist and a frame-accurate plan. 2) Delegate every "
    "change to the project, and the render, to the `editor` sub-agent: give it the brief, the "
    "plan and the paths. Do not edit the project yourself. 3) Ask the `verifier` sub-agent to "
    "check the project and the render against the brief. 4) If it reports failures, send precise "
    "fixes to the editor and verify again (at most two rounds). 5) Report to the user item by item."
)


def setup(arm: str, work: Path) -> dict[str, list[str]]:
    """Write the arm's files into the work folder; return its tools, args and server command."""
    if arm == "playbook":
        skill = work / ".claude" / "skills" / "kdenlive-editing"
        skill.mkdir(parents=True)
        shutil.copy(HERE / "arms" / "playbook.md", skill / "SKILL.md")
        return {"allow": ["Skill"], "deny": ["Agent", "Task"], "args": []}
    if arm == "subagents":
        agents = work / ".claude" / "agents"
        agents.mkdir(parents=True)
        for name in ("editor", "verifier"):
            shutil.copy(HERE / "arms" / f"{name}.md", agents / f"{name}.md")
        return {
            "allow": ["Agent", "Task"],
            "deny": [],
            "args": ["--append-system-prompt", ORCHESTRATOR],
        }
    if arm == "instructions":  # the playbook as the server's own MCP instructions
        return {
            "allow": [],
            "deny": ["Agent", "Task"],
            "args": [],
            "server": [sys.executable, str(HERE / "server_with_playbook.py")],
        }
    return {"allow": [], "deny": ["Agent", "Task"], "args": []}


def client(
    text: str,
    work: Path,
    extra: dict[str, list[str]],
    model: str | None,
    resume: str | None,
    tag: str,
) -> list[dict]:
    config = work / ".mcp-kdenlive.json"
    command = extra.get("server") or [str(rc.SERVER)]
    env = {"KDENLIVE_MCP_ALLOWED_ROOTS": str(work)}
    for key in ("DISPLAY", "WAYLAND_DISPLAY", "XAUTHORITY"):
        if os.environ.get(key):
            env[key] = os.environ[key]
    config.write_text(
        json.dumps(
            {"mcpServers": {"kdenlive": {"command": command[0], "args": command[1:], "env": env}}}
        )
    )
    args = [
        shutil.which("claude") or "claude",
        "-p",
        text,
        "--mcp-config",
        str(config),
        "--strict-mcp-config",
        "--allowedTools",
        ",".join(["mcp__kdenlive", *extra["allow"]]),
        "--disallowedTools",
        ",".join(DISALLOWED + extra["deny"]),
        "--output-format",
        "stream-json",
        "--verbose",
        *extra["args"],
    ]
    if model:
        args += ["--model", model]
    if resume:
        args += ["--resume", resume]
    done = subprocess.run(  # noqa: S603 - argument list, no shell
        args,
        cwd=work,
        capture_output=True,
        text=True,
        timeout=3600,
        check=False,
        stdin=subprocess.DEVNULL,
        env={k: v for k, v in os.environ.items() if k in rc.CLIENT_ENV or k.startswith("LC_")},
    )
    (work / f"claude-{tag}.jsonl").write_text(done.stdout, encoding="utf-8")
    if done.stderr.strip():
        (work / f"claude-{tag}.stderr").write_text(done.stderr, encoding="utf-8")
    return [json.loads(line) for line in done.stdout.splitlines() if line.startswith("{")]


def extra_stats(events: list[dict]) -> dict[str, Any]:
    result = next((e for e in reversed(events) if e.get("type") == "result"), {})
    init = next((e for e in events if e.get("subtype") == "init"), {})
    sub_calls = skill_calls = agent_calls = 0
    for event in events:
        message = event.get("message")
        for part in (message or {}).get("content", []) if isinstance(message, dict) else []:
            if isinstance(part, dict) and part.get("type") == "tool_use":
                if event.get("parent_tool_use_id"):
                    sub_calls += 1
                if part.get("name") == "Skill":
                    skill_calls += 1
                if part.get("name") in ("Agent", "Task"):
                    agent_calls += 1
    usage = result.get("usage") or {}
    return {
        "subagent_tool_calls": sub_calls,
        "skill_invocations": skill_calls,
        "agent_invocations": agent_calls,
        "permission_denials": [d.get("tool_name") for d in result.get("permission_denials") or []],
        "input_tokens": usage.get("input_tokens"),
        "cache_read_tokens": usage.get("cache_read_input_tokens"),
        "cache_write_tokens": usage.get("cache_creation_input_tokens"),
        "output_tokens": usage.get("output_tokens"),
        "model_usage": result.get("modelUsage"),
        "agents_loaded": init.get("agents"),
        "skills_loaded": init.get("skills"),
        "is_error": result.get("is_error"),
    }


def run_one(runs: Path, demo: str, arm: str, rep: int, model: str | None) -> dict[str, Any]:
    work = runs / f"{demo}-{arm}-{rep}"
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    rc.make_media(demo, work)
    if demo == "rescue_project":
        shutil.copy(work / "rescue.kdenlive", work / ".orchestration-original.kdenlive")
    extra = setup(arm, work)
    started = time.monotonic()
    first = client(rc.prompt(demo, work), work, extra, model, None, "first")
    parts = [first]
    if demo == "interview":  # the brief's second part, after the user moved the media
        (work / "archive" / "2026").mkdir(parents=True, exist_ok=True)
        shutil.move(work / "media", work / "archive" / "2026" / "media")
        session = rc.summarise(first)["session"]
        parts.append(client(rc.prompt(demo, work, 1), work, extra, model, session, "second"))
    summaries = [rc.summarise(p) | extra_stats(p) for p in parts]
    answer = "\n".join(s.get("answer") or "" for s in summaries)
    record = {
        "demo": demo,
        "arm": arm,
        "rep": rep,
        "work": str(work),
        "wall_s": round(time.monotonic() - started),
        "turns": sum(s.get("turns") or 0 for s in summaries),
        "cost_usd": round(sum(s.get("cost_usd") or 0 for s in summaries), 3),
        "tool_calls": sum(len(s["tool_calls"]) for s in summaries),
        "server_errors": sum(len(s["errors"]) for s in summaries),
        "runs": summaries,
        "score": score.score(demo, work, answer),
    }
    (work / "result.json").write_text(json.dumps(record, indent=1, default=str), encoding="utf-8")
    with (runs / "results.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, default=str) + "\n")
    s = record["score"]
    print(
        f"{demo:15} {arm:9} #{rep}  {s['passed']:2}/{s['total']:2}  {record['cost_usd']:6.2f}$  "
        f"{record['wall_s']:4}s  {record['tool_calls']:3} calls",
        flush=True,
    )
    return record


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("runs", type=Path)
    parser.add_argument("--demos", default="all")
    parser.add_argument("--arms", default="all")
    parser.add_argument("--reps", type=int, default=1)
    parser.add_argument("--first-rep", type=int, default=1)
    parser.add_argument("--jobs", type=int, default=3)
    parser.add_argument("--model")
    opts = parser.parse_args()
    demos = DEMOS if opts.demos == "all" else tuple(opts.demos.split(","))
    arms = ARMS if opts.arms == "all" else tuple(opts.arms.split(","))
    runs = opts.runs.resolve()
    runs.mkdir(parents=True, exist_ok=True)
    jobs = [
        (d, a, r)
        for r in range(opts.first_rep, opts.first_rep + opts.reps)
        for d in demos
        for a in arms
    ]
    with ThreadPoolExecutor(max_workers=opts.jobs) as pool:
        for future in [pool.submit(run_one, runs, d, a, r, opts.model) for d, a, r in jobs]:
            try:
                future.result()
            except Exception as exc:  # keep the other runs going
                print(f"run failed: {type(exc).__name__}: {exc}", flush=True)


if __name__ == "__main__":
    main()
