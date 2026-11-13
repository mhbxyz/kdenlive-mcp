# Orchestrating an assistant over kdenlive-mcp: experiment and recommendation

Question: which way of driving an assistant gets the most out of this server: one agent, one
agent with a written method (server instructions or a skill), or an orchestrator with
sub-agents? Primary sources are in [orchestration-sources.md](orchestration-sources.md). This
note reports a controlled comparison on the four demos and the recommendation that follows.

## Set-up (2026-11-13)

- **Client:** Claude Code 2.1.321, headless (`claude -p --output-format stream-json`), default
  model (Claude Opus 5.5). Only this server (`--strict-mcp-config`), from this checkout.
  Shell, file writing and web tools disallowed in every arm.
- **Tasks:** the four demo briefs (`examples/demos/*/prompt.md`), fresh media for every run.
  The interview brief has a second turn after the media folder is moved.
- **Arms** (`examples/orchestration/`):

  | Arm | What changes |
  |---|---|
  | `single` | Nothing: one agent, the server's current 287-character instructions. Sub-agents disabled. |
  | `instructions` | The same agent; the server's instructions carry a 2.3 kB method (`arms/playbook.md` without its header). |
  | `playbook` | The same agent; the method as a project skill (`.claude/skills/kdenlive-editing`). It was invoked in every run. |
  | `subagents` | An orchestrator told to plan, delegate every change and the render to an `editor` sub-agent (edit tools), then have a read-only `verifier` sub-agent check the result, with up to two fix rounds. |

- **Runs:** 4 demos × 4 arms × 2 repetitions = 32 runs, three at a time.
- **Scoring** (`score.py`): 11 to 14 objective checks per demo, derived from the brief. They read
  the project back through the server (clips, order, lengths, crossfades, levels, fades,
  titles, subtitles, guides, missing media, relinking) and measure the render (size, streams,
  duration, picture, fades, loudness). Calibrated on the deterministic demo scripts: 50/50.
  A negative control (the rescue project left untouched) scores 1/7. One check was dropped after
  review because it judged the title's position, which the brief leaves open.
- **Not covered:** other clients (Codex CLI, OpenCode, Grok Build), other models, harder briefs.
  Two repetitions are enough to see large differences, not small ones.

## Results

| Arm | Checks passed | Cost (sum, USD) | Wall time (sum) | Tool calls | Server errors |
|---|---|---|---|---|---|
| `single` | 93/94 | 12.66 | 1247 s | 367 | 11 |
| `instructions` | 93/94 | 11.83 (−7 %) | 1214 s (−3 %) | 343 | 2 |
| `playbook` | 92/94 | 11.21 (−11 %) | 1113 s (−11 %) | 342 | 0 |
| `subagents` | 92/94 | 17.14 (+35 %) | 2167 s (+74 %) | 521 | 6 |

Cost is Claude Code's `total_cost_usd` (API-equivalent), sub-agents included.

Per demo, two runs each (checks · cost · time):

| Demo | `single` | `instructions` | `playbook` | `subagents` |
|---|---|---|---|---|
| Travel vlog | 14/14 · 2.37/2.14 $ · 288/253 s | 14/14 · 2.34/2.32 $ · 293/286 s | 14/14 · 2.04/2.31 $ · 248/258 s | 14/14 · 2.43/2.44 $ · 358/368 s |
| Interview (2 turns) | 11, 10/11 · 2.82/1.87 $ · 210/132 s | 10, 11/11 · 2.08/2.05 $ · 146/144 s | 10/11 · 1.95/1.82 $ · 137/124 s | 10/11 · 3.36/3.61 $ · 306/309 s |
| Rescue | 11/11 · 0.91/0.76 $ · 93/81 s | 11/11 · 0.64/0.65 $ · 68/76 s | 11/11 · 0.63/0.64 $ · 73/73 s | 11/11 · 1.38/1.33 $ · 204/200 s |
| Vertical short | 11/11 · 0.87/0.93 $ · 92/98 s | 11/11 · 0.88/0.89 $ · 103/98 s | 11/11 · 0.89/0.93 $ · 98/102 s | 11/11 · 1.34/1.26 $ · 214/208 s |

### What the numbers say

- **Quality is at the ceiling in every arm.** The only check that fails is "both voices about as
  loud" in the interview (6 of 8 runs). It is a **missing capability**, not an orchestration
  problem: no tool measures loudness (#102). Every run said so and gave its boost (+6 or +9 dB)
  as an estimate. The two runs that passed differ: one improvised a measurement (rendering each
  voice to FLAC and comparing file sizes, then +17 dB, leaving test files behind); the other
  guessed +12 dB, which happened to land within 4 dB, and said it was a guess.
- **A written method removes mistakes and some cost.** With no method the agent made six real
  mistakes in four runs: writing into folders that do not exist, writing over the source without
  `overwrite`, a stale `expected_sha256`, a render to an extension the server refuses. With the
  method (as instructions or as a skill) there were none; the remaining errors are the harmless
  "project does not exist yet" checks of the interview brief. Cost fell 7–11 % overall and
  25–30 % on the rescue (the most procedural brief).
- **Sub-agents cost more and add nothing here.** +35 % cost and +74 % wall time, the same checks
  passed. The verifier did not catch the one real gap, because it is a capability gap, and it
  found nothing else to fix. This matches Anthropic's guidance that tasks whose steps depend on
  each other (every edit needs the previous revision) are a poor fit for multi-agent set-ups
  ([sources §3.2](orchestration-sources.md)).
- **Instructions vs skill: about the same.** The skill run was slightly cheaper, within the
  spread of two runs. The instructions reach every client that reads them (Claude Code, Codex,
  OpenCode; Grok undocumented); the skill must be installed per client.

## Recommendation

1. **One agent, with the method in the server's instructions.** Ship the playbook (plan in
   frames, one working file edited in place with chained revisions, check every brief item
   before rendering, honest report) as the server's MCP instructions, within Claude Code's
   4,096-character limit and with the essentials in the first 512 characters (Codex's advice).
   This is the cheapest lever, needs nothing from the user, and works across clients.
2. **Offer the same method as a skill** for users who want it in their client explicitly
   (`.claude/skills`, `.agents/skills`, `.opencode/skills`). Optional; same effect in this test.
3. **Do not recommend an orchestrator with sub-agents** for editing one project. Keep sub-agents
   for side tasks with their own context if they ever pay off (probing many files, comparing
   alternatives), and re-test a single read-only verifier once the server can measure sound and
   look at frames, so it can check what the editor cannot.
4. **Spend effort on capabilities, not orchestration:** loudness (#102) is the only failing check;
   frames (#101) would let the agent see titles and crops; subtitles from text (#116).
5. **Annotations:** every edit tool is `destructiveHint=true`, even when it writes a new copy.
   In Codex's `auto` mode that asks for approval on every edit (sources §5.1). Worth revisiting
   with care, since annotations are hints a client may trust.

## Reproducing

```sh
uv run python examples/orchestration/bench.py RUNS --demos all --arms all --reps 2 --jobs 3
uv run python examples/orchestration/rescore.py RUNS      # after a change to score.py
```

Each run folder keeps the client's stream (`claude-*.jsonl`), the project, the render and
`result.json` (summary, tokens, cost, sub-agent and skill use, score with notes). Runs use
the caller's Claude Code login; this series cost about 56 USD API-equivalent (pilot included).
