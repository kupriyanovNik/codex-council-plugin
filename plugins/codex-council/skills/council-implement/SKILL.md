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
3. Create one focused task per write slice with `create_task`.
4. Require Writer to call `claim_task` before editing.
5. Do not run parallel Writers on overlapping files.
6. Require Writer to post a completion artifact with changed files and
   verification commands or checks.
7. Require Reviewer and Verifier to inspect the result before final synthesis.
8. Export transcript and report changed files, checks run, and open risks.

## Safety Rules

- If user permission to edit is ambiguous, stop and ask.
- If a task lease is held by another agent, do not edit that task.
- If verification fails, keep the session open and summarize the blocker.
