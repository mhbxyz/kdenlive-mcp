# Orchestrating an AI assistant over kdenlive-mcp: primary sources

Research notes. Date: 2026-11-13. Question: what is the best way to have an assistant use this server? One agent or an orchestrator with sub-agents, workflows, skills, MCP prompts, resources, server instructions, hooks.

This file records what primary sources say, with links. It does not decide. Section 7 separates facts from inferences and lists experiments worth running.

The experiment that tests these hypotheses, and the recommendation, are in
[orchestration.md](orchestration.md).

## 1. Scope and method

- **Sources:** official specifications, official product docs, first-party engineering posts, and source code. Third-party write-ups were not used as evidence.
- **Access dates:** pages were read on 2026-11-12 (MCP spec pages marked so) or 2026-11-13 (everything else).
- **Versions seen:**

| Source | Version or date observed |
|---|---|
| MCP specification | current revision **2026-07-28** (previous: 2025-11-25) |
| Claude Code docs | undated; pages cite features up to v2.1.304. Repo tests used 2.1.319 |
| Codex CLI | latest release `rust-v0.167.2` (2026-11-11); source read at `openai/codex@16e2762` (2026-11-12). Docs moved to `learn.chatgpt.com` |
| OpenCode | repo is now `anomalyco/opencode`; latest release v2.2.7 (2026-11-10); source read at `6dd4063` (2026-11-12). Repo tests used 1.19.4 |
| Grok Build CLI | `docs.x.ai/build/*`, undated |
| kdenlive-mcp | this repo at `441dfe3`, MCP Python SDK 2.3.0 |

- **"Not documented"** means the owner's docs and, where read, its source say nothing. It does not mean "unsupported".

## 2. MCP specification (revision 2026-07-28)

### 2.1 Protocol shape changed in 2026-07-28

- The `initialize` handshake is removed. Every request carries its protocol version and client capabilities in `_meta`. Servers MUST implement `server/discover`, which returns versions, capabilities, identity and `instructions`.
- Protocol-level sessions are removed. "Servers that need cross-call state use explicit, server-minted handles passed as ordinary tool arguments."
- Server-initiated requests (`roots/list`, `sampling/createMessage`, `elicitation/create`) are replaced by Multi Round-Trip Requests: the server returns `resultType: "input_required"` and the client retries with `inputResponses`.
- Tasks moved out of core into the official extension `io.modelcontextprotocol/tasks` (polling with `tasks/get`, input with `tasks/update`).
- **Deprecated:** Roots, Sampling and Logging. Suggested migration for Roots: "pass directories or files via tool parameters, resource URIs, or server configuration".
- Stdio clients that support both eras SHOULD send `server/discover` first, then fall back to `initialize`.

Sources: [Key changes](https://modelcontextprotocol.io/specification/latest/changelog), [Versioning](https://modelcontextprotocol.io/specification/versioning), [Discovery](https://modelcontextprotocol.io/specification/2026-07-28/server/discover) (accessed 2026-11-12).

### 2.2 Features relevant to orchestration

| Feature | What the spec says |
|---|---|
| Server `instructions` | Optional string in `DiscoverResult` (formerly `InitializeResult`): "Natural-language guidance describing the server and its features. This can be used by clients to improve an LLM's understanding of available tools (e.g., by including it in a system prompt)… should not duplicate information already in tool descriptions." |
| Prompts | "**user-controlled**… typically… triggered through user-initiated commands… For example, as slash commands." `prompts/get` returns messages; arguments supported. |
| Resources | "**application-driven**, with host applications determining how to incorporate context." Templates (RFC 6570) supported. Subscriptions now go through `subscriptions/listen` with `resourceSubscriptions`. |
| Tool annotations | Hints only. `readOnlyHint` (default false), `destructiveHint` (default true, meaningful only when not read-only), `idempotentHint` (default false), `openWorldHint` (default true). "Clients should never make tool use decisions based on `ToolAnnotations` received from untrusted servers." |
| Structured output | `structuredContent` may be any JSON value. With an `outputSchema`, servers MUST conform. "For backwards compatibility, a tool that returns structured content SHOULD also return the serialized JSON in a TextContent block." |
| Tool errors | Execution errors use `isError: true`. "Clients SHOULD provide tool execution errors to language models to enable self-correction." |
| Stateful tools | Non-normative: return a handle from a creation tool and accept it on later calls; "The model is responsible for carrying [the handle] forward." Expired handles should return an execution error. |
| Progress | Client sends `_meta.progressToken`; server MAY send `notifications/progress` with increasing `progress`, optional `total`, `message`. |
| Cancellation | `notifications/cancelled` with `requestId`; "the result will be unused, so any associated processing SHOULD cease." |
| Elicitation | Form or URL mode, now delivered inside `InputRequiredResult`. Form mode MUST NOT ask for secrets. |
| Sampling, roots | Still specified, now **Deprecated**. |
| Tasks | Extension `io.modelcontextprotocol/tasks`, stage "Beta, to Core". |
| Skills over MCP | Extension `io.modelcontextprotocol/skills` (Beta; SEP-2640 Final). Servers expose Agent Skills through `skills/list`, `skills/get` and `resources/read` (`skill://` URIs). Hosts must verify digests and get user approval. |

Sources: [Tools](https://modelcontextprotocol.io/specification/2026-07-28/server/tools), [schema.ts](https://github.com/modelcontextprotocol/modelcontextprotocol/blob/main/schema/2026-07-28/schema.ts) (`ToolAnnotations`, `DiscoverResult`, `CancelledNotification`), [Progress](https://modelcontextprotocol.io/specification/2026-07-28/basic/utilities/progress) (accessed 2026-11-12); [Prompts](https://modelcontextprotocol.io/specification/2026-07-28/server/prompts), [Resources](https://modelcontextprotocol.io/specification/2026-07-28/server/resources), [Elicitation](https://modelcontextprotocol.io/specification/2026-07-28/client/elicitation), [Extensions overview](https://modelcontextprotocol.io/docs/extensions/overview), [Skills extension](https://modelcontextprotocol.io/extensions/skills/overview) (accessed 2026-11-13).

The community-maintained [extension client matrix](https://modelcontextprotocol.io/extensions/client-matrix) (source `docs/extensions/client-matrix.mdx`, accessed 2026-11-13) marks Claude Code as supporting Tasks and no other extension. Codex, OpenCode and Grok are not listed. It is community-maintained, so the per-client sections below take precedence.

## 3. Anthropic guidance

### 3.1 Building effective agents (2024-12-19)

- **Definitions:** workflows are "systems where LLMs and tools are orchestrated through predefined code paths"; agents are "systems where LLMs dynamically direct their own processes and tool usage".
- **Default:** "we recommend finding the simplest solution possible, and only increasing complexity when needed."
- **Patterns and when they fit:**
  - prompt chaining, "when the task can be easily and cleanly decomposed into fixed subtasks";
  - routing, "where there are distinct categories that are better handled separately";
  - parallelization, "when the divided subtasks can be parallelized for speed";
  - orchestrator-workers, "for complex tasks where you can't predict the subtasks needed";
  - evaluator-optimizer, "particularly effective when we have clear evaluation criteria".
- **Agents:** for "open-ended problems where it's difficult or impossible to predict the required number of steps". They should "gain 'ground truth' from the environment at each step"; use "stopping conditions (such as a maximum number of iterations)".
- **Tools:** poka-yoke, "Change the arguments so that it is harder to make mistakes." For SWE-bench, "we actually spent more time optimizing our tools than the overall prompt."

Source: [anthropic.com/engineering/building-effective-agents](https://www.anthropic.com/engineering/building-effective-agents) (accessed 2026-11-13).

### 3.2 How we built our multi-agent research system (2025-06-13)

- **Cost:** "agents typically use about 4× more tokens than chat interactions, and multi-agent systems use about 15× more tokens than chats."
- **Benefit:** an Opus 4 lead with Sonnet 4 subagents "outperformed single-agent Claude Opus 4 by 90.2% on our internal research eval". Such systems "excel especially for breadth-first queries that involve pursuing multiple independent directions simultaneously".
- **Why:** "three factors explained 95% of the performance variance in the BrowseComp evaluation"; "token usage by itself explains 80% of the variance". Also: "upgrading to Claude Sonnet 4 is a larger performance gain than doubling the token budget on Claude Sonnet 3.7".
- **Poor fits:** "some domains that require all agents to share the same context or involve many dependencies between agents are not a good fit"; "most coding tasks involve fewer truly parallelizable tasks than research"; "LLM agents are not yet great at coordinating and delegating to other agents in real time."
- **Economics:** multi-agent systems "require tasks where the value of the task is high enough to pay for the increased performance."
- **Coordination:** "Without detailed task descriptions, agents duplicate work, leave gaps, or fail to find necessary information." Lead agents "execute subagents synchronously". Prefer artifacts that subagents persist, with lightweight references passed back.
- **Effort scaling:** "Simple fact-finding requires just 1 agent with 3-10 tool calls"; comparisons "2-4 subagents with 10-15 calls each".
- **State:** "Agents are stateful and errors compound."
- **Evaluation:** start with "about 20 queries representing real usage patterns"; LLM-as-judge for outputs; for agents that "modify persistent state across multi-turn conversations": "We found success focusing on end-state evaluation rather than turn-by-turn analysis."

Source: [anthropic.com/engineering/multi-agent-research-system](https://www.anthropic.com/engineering/multi-agent-research-system) (accessed 2026-11-13).

### 3.3 Effective context engineering for AI agents (2025-09-29)

- **Goal:** "find the smallest set of high-signal tokens that maximize the likelihood of your desired outcome". Context rot: as context grows, "the model's ability to accurately recall information from that context decreases".
- **System prompts:** aim for "the right altitude", between brittle hard-coded logic and vague guidance.
- **Tools:** curate "a minimal viable set of tools"; "bloated tool sets" lead to "ambiguous decision points about which tool to use".
- **Just in time:** keep "lightweight identifiers" and load data through tools at runtime.
- **Long tasks:** compaction suits "extensive back-and-forth"; structured note-taking "excels for iterative development with clear milestones"; multi-agent suits "complex research and analysis where parallel exploration pays dividends". A sub-agent "returns only a condensed, distilled summary of its work (often 1,000-2,000 tokens)".

Source: [effective-context-engineering-for-ai-agents](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents) (accessed 2026-11-13).

### 3.4 Writing effective tools for agents (2025-09-11)

- **Evaluate with realistic tasks:** "Each evaluation prompt should be paired with a verifiable response or outcome." Track runtime, "the total number of tool calls, the total token consumption, and tool errors". Use held-out test sets.
- **Fewer, higher-level tools:** "More tools don't always lead to better outcomes"; tools "can consolidate functionality, handling potentially multiple discrete operations".
- **Responses:** "return only high signal information"; offer a `response_format` (concise/detailed); paginate, filter, truncate. "For Claude Code, we restrict tool responses to 25,000 tokens by default."
- **Errors:** "clearly communicate specific and actionable improvements".
- **Descriptions:** "Even small refinements to tool descriptions can yield dramatic improvements."

Source: [writing-tools-for-agents](https://www.anthropic.com/engineering/writing-tools-for-agents) (accessed 2026-11-13).

### 3.5 Agent Skills

- **Format:** "A skill is a directory containing a SKILL.md file" with `name` and `description` frontmatter.
- **Progressive disclosure:** metadata is always loaded (~100 tokens per skill), the body when triggered (under 5k tokens), bundled files as needed. Scripts run "and only their output enters context".
- **Relation to MCP:** skills can "complement Model Context Protocol (MCP) servers by teaching agents more complex workflows".
- **Open standard:** published 2025-12-18 at agentskills.io. The spec limits `name` to 64 characters and `description` to 1,024. It recommends a body under 5,000 tokens and "Keep your main `SKILL.md` under 500 lines".
- **Security:** install skills only from trusted sources.

Sources: [equipping-agents-for-the-real-world-with-agent-skills](https://www.anthropic.com/engineering/equipping-agents-for-the-real-world-with-agent-skills) (2025-10-16, updated 2025-12-18), [Agent Skills overview](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/overview), [agentskills.io/specification](https://agentskills.io/specification) (all accessed 2026-11-13).

## 4. Claude Code

All from [code.claude.com/docs/en](https://code.claude.com/docs/en/) pages `mcp`, `sub-agents`, `skills`, `hooks`, `headless`, `cli-reference`, `output-styles`, `workflows`, `costs` (Markdown versions, accessed 2026-11-13).

### 4.1 MCP

- **Server instructions and tool search:** tool search is on by default, so MCP tools are deferred. "Only tool names and server instructions load at session start." Server instructions "help Claude understand when to search for your tools". Server instructions are cut at **4,096 characters**; deferred tool descriptions at 16,384.
- **Load a tool upfront:** `_meta["anthropic/alwaysLoad"]: true` on a tool. `ENABLE_TOOL_SEARCH` (`false`, `auto`, `auto:N`) controls deferral.
- **Annotations:** `readOnlyHint` and `idempotentHint` mark tools safe to repeat after a dropped connection, a retry Claude Code does for HTTP servers and claude.ai connectors (not stdio). With `readOnlyHint`, "Claude Code also runs the tool's calls in parallel with other read-only calls." Use of `destructiveHint` is not documented.
- **Forced approval:** `_meta["anthropic/requiresUserInteraction"]: true` prompts on every call, even with allow rules.
- **Prompts:** listed as `/servername:promptname (MCP)`, also runnable as `/mcp__servername__promptname`. Arguments are split on whitespace.
- **Resources:** `@server:protocol://path` mentions attach them. "Claude Code automatically provides tools to list and read MCP resources when servers support them."
- **Output limits:** warning above 10,000 tokens, limit 25,000 by default (`MAX_MCP_OUTPUT_TOKENS`).
- **Elicitation:** form and URL modes. An `Elicitation` hook can answer automatically. In a non-interactive session, an MCP *task* that requests input is cancelled.
- **Roots:** answers `roots/list` with the launch directory and added directories.
- **Long calls:** a main-conversation call still running after 2 minutes moves to a background task (not in `-p`, not from subagents). Progress notifications reset the idle timeout (30 minutes for stdio). Stopping the task cancels the call.
- **MCP tasks:** public beta, v2.1.304+. Only on protocol 2026-07-28, only from the main conversation, not in a one-shot `claude -p` run.
- **Runtimes:** the v2 runtime (TypeScript SDK 2.0) negotiates revision 2026-07-28 with servers that support it.
- **Not documented:** sampling, structured content handling.

### 4.2 Sub-agents

- **Definition:** Markdown files with YAML frontmatter in `.claude/agents/` or `~/.claude/agents/`, or JSON via `--agents`. Fields include `tools`, `disallowedTools`, `model`, `permissionMode`, `maxTurns`, `skills` (full content preloaded), `mcpServers`, `hooks`, `effort`, `isolation`.
- **MCP per sub-agent:** `tools: mcp__github__*` grants one server's tools; `disallowedTools: mcp__*` withholds all MCP tools. `mcpServers` can define a server inline, connected only for that sub-agent, "The subagent gets the tools; the parent conversation doesn't."
- **Context:** "Each subagent starts with a fresh, isolated context window. It doesn't see your conversation history." It gets its own prompt, the delegation message, CLAUDE.md and git status.
- **When to stay in the main conversation:** "The task needs frequent back-and-forth", "Multiple phases share significant context", or "Latency matters". Use sub-agents when output is verbose, when you want tool restrictions, or when "The work is self-contained and can return a summary."
- **Limits:** nesting up to 3 layers; 20 concurrent sub-agents by default.
- **Cost:** sub-agents "send [their] own requests". Agent teams "use approximately 7x more tokens than standard sessions when teammates run in plan mode" (costs page).

### 4.3 Skills, commands, workflows, output styles

- **Skills:** `.claude/skills/<name>/SKILL.md`; custom commands "have been merged into skills". Description always in context, body loaded when invoked. `disable-model-invocation: true` makes a skill user-only; `allowed-tools` pre-approves tools for that turn; `context: fork` runs the skill in a sub-agent. Description plus `when_to_use` is truncated at 1,536 characters. After compaction, each invoked skill keeps its first 5,000 tokens (25,000 total).
- **Dynamic workflows:** a JavaScript script that orchestrates many sub-agents. The docs compare who decides what runs next: sub-agents and skills, "Claude, turn by turn"; workflows, "The script". Scale: "Dozens to hundreds of agents per run".
- **Output styles:** an output style "is an instruction Claude follows, so nothing enforces it". For "Something to happen every time without exception", use a hook.

### 4.4 Hooks

- **Events** include `PreToolUse`, `PostToolUse`, `PostToolUseFailure`, `PostToolBatch`, `Stop`, `SubagentStop`, `Elicitation`.
- **MCP matchers:** tools are named `mcp__<server>__<tool>`. "`mcp__memory__.*` matches all tools from the `memory` server"; `mcp__memory` alone matches nothing.
- **Decisions:** `PreToolUse` can allow, deny, ask, or rewrite input (`updatedInput`). `PostToolUse` can block, add `additionalContext`, or replace output (`updatedToolOutput`). `Stop` can block so Claude keeps going.
- **`mcp_tool` hooks:** a hook can call a tool on a configured MCP server, for example after each edit.

### 4.5 Headless runs for experiments

- `claude -p … --output-format json` "includes `total_cost_usd` and a per-model cost breakdown" (client-side estimates). `stream-json` gives per-event output.
- Useful flags: `--mcp-config`, `--strict-mcp-config`, `--allowedTools`, `--max-turns`, `--max-budget-usd`, `--agents`, `--append-system-prompt-file`, `--bare`.

## 5. Codex CLI, OpenCode, Grok Build CLI

### 5.1 Codex CLI (OpenAI)

Docs: [MCP](https://learn.chatgpt.com/docs/extend/mcp?surface=cli), [Subagents](https://learn.chatgpt.com/docs/agent-configuration/subagents), [AGENTS.md](https://learn.chatgpt.com/docs/agent-configuration/agents-md), [Skills](https://learn.chatgpt.com/docs/build-skills), [Custom prompts](https://learn.chatgpt.com/docs/custom-prompts), [Hooks](https://learn.chatgpt.com/docs/hooks), [Non-interactive mode](https://learn.chatgpt.com/docs/non-interactive-mode) (accessed 2026-11-13). Source: `openai/codex@16e2762`.

- **Server instructions:** Codex uses `instructions` "as server-wide guidance alongside the server's tools". Advice: "Keep the first 512 characters self-contained."
- **MCP config:** `tool_timeout_sec` default **60 s**, `startup_timeout_sec` 10 s, `enabled_tools`/`disabled_tools`, `default_tools_approval_mode` (`auto`, `prompt`, `writes`, `approve`), per-tool `approval_mode` and `output_token_limit`.
- **Annotations (source):** `codex-rs/core/src/mcp_tool_call.rs`. In `writes` mode, a tool needs approval unless `readOnlyHint` is true. In `auto` mode, a read-only tool needs none; otherwise `destructiveHint` (default true) or `openWorldHint` (default true) requires approval.
- **Capabilities (source):** `codex-mcp/src/rmcp_client.rs` declares only `elicitation` (plus extensions) and protocol `2025-06-18` in `initialize`. No sampling or roots capability.
- **Resources (source):** built-in `list_mcp_resources` and `read_mcp_resource` tool handlers exist. Not described in the MCP docs page.
- **Prompts:** no MCP prompt support found in docs or source.
- **Progress (source):** received and logged only (`logging_client_handler.rs`). **Cancellation (source):** dropped requests send cancellation (`request_cancellation.rs`). **Structured content:** passed through to the result (`mcp_tool_call.rs`).
- **AGENTS.md:** concatenated from root to cwd, capped at 32 KiB (`project_doc_max_bytes`).
- **Skills:** Agent Skills format in `.agents/skills`; explicit (`$skill`, `/skills`) or implicit by description. `agents/openai.yaml` can declare an MCP tool dependency.
- **Custom prompts:** "Deprecated. Use skills for reusable prompts."
- **Sub-agents:** TOML files in `.codex/agents/` (`name`, `description`, `developer_instructions`, `model`, `model_reasoning_effort`). They "inherit the parent's permissions, provider, and MCP servers"; `mcp_servers` in an agent file is ignored. Docs: "subagent workflows consume more tokens than comparable single-agent runs".
- **Hooks:** `PreToolUse`/`PostToolUse` match MCP tool names such as `mcp__filesystem__.*`; `mcp_tool` hook handlers exist.
- **Headless:** `codex exec --json` emits JSONL; `turn.completed` carries token `usage`. A cost field is not documented.

### 5.2 OpenCode

Docs: [Agents](https://opencode.ai/docs/agents/), [MCP servers](https://opencode.ai/docs/mcp-servers/), [Commands](https://opencode.ai/docs/commands/), [Skills](https://opencode.ai/docs/skills/) (accessed 2026-11-13). Source: `anomalyco/opencode@6dd4063`, `packages/core/src/mcp/`.

- **Server instructions:** "When a server returns instructions during initialization, OpenCode adds them to the system prompt" (`instructions.ts` wraps them in `<mcp_instructions>`).
- **Prompts:** "MCP prompts become commands named `<server>:<prompt>`."
- **Resources:** built-in `list_mcp_resources` and `read_mcp_resource` tools.
- **Elicitation:** form and URL. **Roots:** answered, except on pooled connections (source).
- **Protocol:** `protocol` defaults to `legacy` (up to 2025-11-25); `auto` probes `server/discover`.
- **Code Mode:** `codemode` "Defaults to `true`": tools are grouped by server and called from code (`tools.<server>.<tool>(...)`). `false` exposes tools directly.
- **Timeouts:** startup 30 s, catalog 30 s, execution 12 h.
- **Agents:** primary or subagent; Markdown in `.opencode/agents/`. Subagents "run with fresh context in their own child sessions". Per-agent `permissions` (allow/ask/deny), `steps` limit, model. Tools are named `<server>_<tool>`; permission globs such as `context7_*` hide or deny them.
- **Commands:** `.opencode/commands/*.md` with `agent`, `model`, `subagent` (run as a child session), `$ARGUMENTS`.
- **Skills:** reads `.opencode/skills`, `.claude/skills` and `.agents/skills`; loaded through a `skill` tool; per-agent `skill` permissions.
- **Not documented:** sampling, progress display, cancellation, tasks, use of annotations. The source sends a `progressToken` on tool calls (test `mcp.test.ts`).

### 5.3 Grok Build CLI (xAI)

Docs: [MCP servers](https://docs.x.ai/build/features/mcp-servers), [Subagents](https://docs.x.ai/build/features/subagents), [Hooks](https://docs.x.ai/build/features/hooks), [Skills](https://docs.x.ai/build/features/skills-plugins-marketplaces), [AGENTS.md](https://docs.x.ai/build/features/project-rules), [Headless](https://docs.x.ai/build/cli/headless-scripting), [CLI reference](https://docs.x.ai/build/cli/reference) (accessed 2026-11-13).

- **MCP:** tools are "namespaced as `<server>__<tool>`"; `tool_timeout_sec` default 6000. Reads `~/.claude.json` and `.mcp.json` too.
- **Sub-agents:** built-in `general-purpose`, `explore`, `plan`; custom types in `.grok/agents/`. Their file format and per-agent tool or MCP restriction are not documented.
- **Skills:** `.grok/skills/`; user-invocable skills become slash commands.
- **AGENTS.md:** reads `AGENTS.md` and `CLAUDE.md` from root to cwd, "with no size cap".
- **Hooks:** `PreToolUse` is "the only blocking event"; hooks fail open. Reads Claude Code hook files.
- **Headless:** `grok -p … --output-format json` ("one JSON object at the end"), `--max-turns`, `--allow`. Cost fields are not documented.
- **Not documented:** server instructions, prompts, resources, elicitation, sampling, roots, annotations, progress, cancellation, tasks.

## 6. Client support matrix for MCP features

Legend: ✅ documented or found in source · 🟡 partial or with conditions · ❌ absent (capability not declared in source) · ❔ not documented.

| Feature | Claude Code | Codex CLI | OpenCode | Grok Build |
|---|---|---|---|---|
| Server `instructions` | ✅ loaded upfront, cut at 4,096 chars | ✅ "first 512 characters" advice | ✅ added to system prompt | ❔ |
| Prompts | ✅ `/server:prompt` | ❔ none found | ✅ commands `<server>:<prompt>` | ❔ |
| Resources | ✅ `@` mentions + list/read tools | 🟡 list/read tools (source only) | ✅ list/read tools | ❔ |
| Resource subscriptions | ❔ | ❔ | ❔ | ❔ |
| Elicitation | ✅ form + URL (interactive) | ✅ (source) | ✅ form + URL | ❔ |
| Sampling (deprecated) | ❔ | ❌ | ❔ | ❔ |
| Roots (deprecated) | ✅ | ❌ | ✅ except pooled | ❔ |
| `readOnlyHint` | ✅ parallel reads; retry (HTTP servers only) | ✅ approval modes | ❔ | ❔ |
| `destructiveHint` | ❔ | ✅ `auto` approval | ❔ | ❔ |
| `idempotentHint` | ✅ retry (HTTP servers only) | ❔ | ❔ | ❔ |
| Structured content | ❔ | ✅ passed through (source) | ✅ (source) | ❔ |
| Progress notifications | ✅ shown in `/tasks`, reset idle timeout | 🟡 logged only | 🟡 token sent | ❔ |
| Cancellation | ✅ on task stop | ✅ on dropped request | ❔ | ❔ |
| Tasks extension | 🟡 beta, 2026-07-28 only, not `-p` | ❔ | ❔ | ❔ |
| Per-sub-agent MCP restriction | ✅ `tools`/`disallowedTools`/`mcpServers` | ❌ inherits parent | ✅ per-agent permissions | ❔ |
| Hooks on MCP tools | ✅ `mcp__srv__.*` | ✅ `mcp__srv__.*` | ❔ (plugins page not read) | 🟡 regex on tool name |
| Skills (Agent Skills format) | ✅ | ✅ | ✅ | ✅ |
| Headless cost in output | ✅ `total_cost_usd` | 🟡 tokens only | ❔ | ❔ |

## 7. Implications for kdenlive-mcp

### 7.1 Facts about this server (repo, `441dfe3`)

- 37 tools, all tools and no prompts or resources (`docs/clients.md`).
- `INSTRUCTIONS` is three sentences (about 280 characters): call `diagnose_environment` first, local files only (`src/mcp_kdenlive/server.py`).
- Annotations: 12 tools use `READ_ONLY` (`readOnlyHint=True, idempotentHint=True`); 20 edit tools use `EDIT` (`destructiveHint=True, idempotentHint=False`); 5 have custom annotations.
- Edits are a revision chain: the source sha256 is required to overwrite, and edits go to a `destination`. Renders run as jobs polled with `get_render_job` (no MCP progress notifications).
- Past real-client runs (`examples/demos/RESULTS.md`, Claude Code 2.1.319, claude-opus-5-5, one agent): 31 to 76 turns, $0.92 to $2.85, 119 to 378 s per demo, 0 or 1 server errors. The client once used a helper sub-agent; it had no shell and could not measure loudness.

### 7.2 Inferences (not verified)

- **Inference: start with one agent.** Editing one project is sequential. Each edit depends on the previous revision. Anthropic names "many dependencies between agents" and shared context as poor fits for multi-agent systems, at about 15× chat tokens. Parallel editors on one project would also fight over the revision chain.
- **Inference: sub-agents fit side tasks only.** Candidates: probing many media files, a read-only verifier that checks the final project and render against the brief, research of presets. These are self-contained and return a summary. In Claude Code a verifier can be held to read-only tools (`tools: mcp__kdenlive__inspect_project, mcp__kdenlive__get_timeline, …`). Codex cannot restrict MCP tools per agent.
- **Inference: server instructions are the most portable lever.** Three of four clients document them (Grok: not documented). Under Claude Code tool search they are what loads upfront. A richer playbook (revision chaining, destination policy, render-then-verify) fits in 4,096 characters, with the essentials in the first 512 for Codex.
- **Inference: a skill is the portable long-form playbook.** All four clients read Agent Skills, from different folders (`.claude/skills`, `.agents/skills`, `.opencode/skills`, `.grok/skills`). Skills over MCP would ship it with the server, but no target client is documented as supporting that extension.
- **Inference: MCP prompts are low priority.** They are user-invoked and only Claude Code and OpenCode document them.
- **Inference: annotations could be more precise.** Edits to a new `destination` are additive, yet `EDIT` says `destructiveHint=True`. In Codex `auto` mode that forces approval. Splitting in-place overwrite from copy-writing would need care, since annotations are untrusted hints.
- **Inference: hooks are for guarantees, not guidance.** A `PostToolUse` hook on `mcp__kdenlive__.*` or a `Stop` hook could force a final `inspect_project` or render check. Grok hooks fail open, so they cannot be the only safety net.
- **Inference: client timeouts favour jobs.** Codex's default `tool_timeout_sec` is 60 s; `start_render` + `get_render_job` avoids it. MCP tasks would help only Claude Code, only on 2026-07-28, and not in `claude -p`.
- **Inference: OpenCode Code Mode changes how tools are called.** It is on by default in current docs (v2.x). The repo tested 1.19.4. Behaviour with this server is untested.

### 7.3 Uncertain or unverified

- Which protocol revision the stdio server negotiates with each client. SDK 2.3.0 contains 2026-07-28 code for HTTP; stdio behaviour was not checked. Grok used 2025-11-25 in `docs/clients.md`.
- Grok documents tool names as `<server>__<tool>`, but `docs/clients.md` used `--allow "mcp__<name>__*"`. Which form Grok matches was not re-checked.
- Codex resource tools and annotation handling come from source, not docs, and may change.
- Claude Code `destructiveHint` and `structuredContent` handling, and elicitation for plain (non-task) calls under `-p`, are not documented.

### 7.4 Hypotheses worth testing

Run each arm on the same demo briefs (`examples/demos/*/prompt.md`) and fixtures, several runs each (Anthropic suggests starting near 20 tasks).

| Arm | Set-up |
|---|---|
| A. Baseline | One agent, current instructions |
| B. Instructions | One agent, richer server instructions (≤ 4,096 chars, key rules in first 512) |
| C. Skill | One agent, same playbook as a skill, B's instructions reduced to a pointer |
| D. Verifier | A or B plus a read-only verifier sub-agent called once at the end |
| E. Orchestrator | Lead agent delegating edit phases to sub-agents that pass revision and path |
| F. Hook | A or B plus a `Stop` or `PostToolUse` hook that runs a check |
| G. Tool search | B with `ENABLE_TOOL_SEARCH=false` vs default (Claude Code only) |

Expected (inference): B or C beats A on errors and turns; D adds cost but catches misses; E costs most and gains little.

What to measure:

- **Objective success:** the deterministic checks the demos already use (re-parse, expected clips, positions, mixes, subtitles, render streams and duration), plus the Kdenlive round trip (`research/lab/verify_roundtrip.py`).
- **Process:** tool calls by name, server errors (`isError`), client refusals, retries, turns.
- **Cost:** tokens and `total_cost_usd` (`claude -p --output-format json`); token usage from `codex exec --json`.
- **Time:** wall time per run, render time separately.
- **Variance:** spread across repeated runs, not only the mean.
