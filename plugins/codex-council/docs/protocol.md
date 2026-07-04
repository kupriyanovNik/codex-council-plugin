# Council Protocol

Use this protocol whenever a Codex Council skill starts a multi-agent session.

## Core Rules

- Use the MCP blackboard as the transport. Do not move long agent output through
  the parent conversation.
- Keep messages short. Put long analysis in artifacts and reference the
  artifact id.
- Acknowledge messages after reading them.
- Register every actor before it writes to MCP. If the parent agent posts,
  acknowledges, stores artifacts, or proposes decisions directly, register it as
  `chair` first.
- Use claims for evidence-backed assertions.
- Use decisions and votes for final recommendations.
- Use tasks and leases for write-capable work.
- Do not enable writer roles unless the user explicitly authorized code changes.

## Session Bootstrap

Call `create_session` with:

- `workspace_root`: absolute path to the target repo or project.
- `objective`: the user's task.
- `mode`: `light`, `standard`, `deep`, `review`, `design`, or `implement`.
- `allow_writes`: `true` only after explicit user permission to edit files.

Then spawn the role agents and give each agent:

- the workspace root
- the session id
- its role name
- the allowed capabilities
- a reminder to use the MCP tools rather than parent-relayed content

Register `chair` when the parent will write to MCP directly.

Use `subagent-prompts.md` for dispatch templates.

## Rounds

### Round 1: Independent Work

Each role posts its independent position as a short message and stores its full
analysis as an artifact.

### Round 2: Cross Review

Agents list unread messages, read referenced artifacts when relevant, acknowledge
messages, and post challenges or agreements.

### Round 3: Evidence Pass

Evidence-oriented roles check files, tests, docs, or local behavior and append
claims with artifact references.

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

Do not run parallel writers on overlapping files.
