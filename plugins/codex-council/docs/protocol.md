# Council Protocol

Use this protocol whenever a Codex Council skill starts a multi-agent session.

## Core Rules

- Use the MCP blackboard as the transport. Do not move long agent output through
  the parent conversation.
- Subagents must discover the typed Council MCP tools before using the
  blackboard. Their first tool step should be `tool_search` with query
  `codex-council`, then they must call `mcp__codex_council.*` tools directly.
- Do not use shell, Python, sqlite3, or direct stdio calls to
  `mcp/council_server.py` as the normal subagent transport. If typed MCP tools
  cannot be discovered, the subagent should return `BLOCKED` with the discovery
  error instead of falling back to stdio. Direct stdio is reserved for explicit
  diagnostics. See `troubleshooting.md` for stale thread/tool-index symptoms
  after plugin reinstall.
- Keep messages short. Put long analysis in artifacts and reference the
  artifact id.
- Acknowledge messages after reading them.
- Register every actor before it writes to MCP. If the parent agent posts,
  acknowledges, stores artifacts, or proposes decisions directly, register it as
  `chair` first.
- Use claims for evidence-backed assertions.
- Use decisions and votes for final recommendations.
- Use tasks and leases for write-capable work.
- Do not enable writer roles unless the user explicitly authorized file changes.

## Session Bootstrap

Call `create_session` with:

- `workspace_root`: absolute path to the session workspace. For code tasks this
  is usually the target repo. For conceptual tasks it is the local folder where
  council artifacts should be stored.
- `objective`: the user's task.
- `mode`: `light`, `standard`, `deep`, `review`, `design`, or `implement`.
- `allow_writes`: `true` only after explicit user permission to edit files.

Then spawn the role agents and give each agent:

- the workspace root
- the session id
- its role name
- the allowed capabilities
- a reminder to call `tool_search` for `codex-council` and then use typed
  `mcp__codex_council.*` tools rather than parent-relayed content
- a reminder to report `BLOCKED` if typed tools are unavailable, not to use
  shell or stdio fallback

Register `chair` when the parent will write to MCP directly.

Use `subagent-prompts.md` for dispatch templates.
Use `troubleshooting.md` when a subagent cannot discover
`mcp__codex_council.*` after `tool_search`.

## Rounds

### Round 1: Independent Work

Each role posts its independent position as a short message and stores its full
analysis as an artifact.

### Round 2: Cross Review

Agents list unread messages, read referenced artifacts when relevant, acknowledge
messages, and post challenges or agreements.

### Round 3: Evidence Pass

Evidence-oriented roles check the task context, provided material, files, docs,
commands, tests, or local behavior when those sources apply. They append claims
with artifact references and state limits when the session is mostly conceptual.

### Round 4: Decision

The chair or parent proposes a decision. Agents vote when their role has enough
evidence. The parent exports the transcript and writes the user-facing synthesis.

## Message Conventions

Use stable agent ids:

- `architect`
- `skeptic`
- `verifier`
- `reviewer`
- `security`
- `writer`
- `chair`

Use topics:

- `proposal`
- `challenge`
- `evidence`
- `discussion`
- `option`
- `decision`
- `implementation`
- `test`

## Writer Mode

Writer mode is opt-in. It requires:

1. A user request that explicitly permits edits.
2. `allow_writes: true` in the session.
3. A task created with `create_task`.
4. A successful `claim_task` result before editing.
5. A completion artifact describing changed files and verification.

Do not run parallel writers on overlapping files or artifacts.
