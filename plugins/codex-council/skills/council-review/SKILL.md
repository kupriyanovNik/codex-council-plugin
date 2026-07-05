---
name: council-review
description: Run a read-only Codex Council review of an idea, answer, document, plan, decision, codebase, or diff using role-based subagents and the local MCP blackboard. Use when the user wants multiple independent reviewer perspectives without file changes.
---

# Council Review

Use this skill for advisory review only. Do not edit files.

## Required References

Read `../../docs/protocol.md`, `../../docs/roles.md`, and
`../../docs/subagent-prompts.md`.

## Workflow

1. Create a council session with `mode: review` and `allow_writes: false`.
2. Use roles: Reviewer, Skeptic, Verifier. Add Security when security or privacy
   risk matters.
3. Ask Verifier to ground claims in the task context, provided material, files,
   commands, docs, or runtime evidence when available.
4. Ask Skeptic to challenge Reviewer and Verifier claims.
5. Export the transcript and return findings ordered by severity or decision
   impact.

## Output Shape

Lead with findings. Include references when available, transcript path, and
evidence gaps. Use file references and test gaps only when the reviewed material
is code or a local project.
