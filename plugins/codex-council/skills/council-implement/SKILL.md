---
name: council-implement
description: Run a write-capable Codex Council workflow with a Writer role, task leases, review roles, and MCP-backed artifacts. Use only when the user explicitly asks Codex to change files, such as code, docs, configs, plans, or generated artifacts.
---

# Council Implement

Use this skill only after the user explicitly authorized file changes.

## Required References

Read `../../docs/protocol.md`, `../../docs/roles.md`, and
`../../docs/subagent-prompts.md`.

## Workflow

1. Create a session with `mode: implement` and `allow_writes: true`.
2. Use Architect, Writer, Reviewer, Verifier, and Skeptic.
3. Register the parent as `chair` with the returned `registration_token` before
   creating write tasks. Keep the returned chair `agent_token` private.
4. Register Writer from the parent with the returned `registration_token`.
   Keep the returned Writer `agent_token` private and pass only that
   per-agent token directly to the Writer subagent.
5. Create one focused task per write slice with `create_task`, using the chair
   or writer `agent_token`.
6. Require Writer to call `claim_task` with its private `agent_token` before
   editing.
7. Require Writer to include its private `agent_token` on every mutating Council
   call it makes as `writer`, including heartbeat, messages, acknowledgements,
   artifacts, claims, decisions, and task tools.
   If Writer repeats `register_agent`, it must include the same private
   `agent_token`.
8. Do not run parallel Writers on overlapping files.
9. Require Writer to post a completion artifact with changed files and
   verification commands or checks.
10. Require Reviewer and Verifier to inspect the result before final synthesis.
11. Export transcript and report changed files, checks run, and open risks.

## Safety Rules

- If user permission to edit is ambiguous, stop and ask.
- If a task lease is held by another agent, do not edit that task.
- If verification fails, keep the session open and summarize the blocker.
- Do not place registration tokens or agent tokens in messages, artifacts,
  transcripts, or user-facing output.
- Never pass the session-wide `registration_token` to Writer or any other
  subagent.
- Close, cancel, or archive sessions only from the parent with the private
  `registration_token` or from a Chair identity with its private `agent_token`.
