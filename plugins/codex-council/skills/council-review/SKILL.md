---
name: council-review
description: Run a read-only Codex Council review of a codebase, diff, design, document, or plan using role-based subagents and the local MCP blackboard. Use when the user wants multiple independent reviewer perspectives without code changes.
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
3. Ask Verifier to ground claims in files, commands, docs, or runtime evidence.
4. Ask Skeptic to challenge Reviewer and Verifier claims.
5. Export the transcript and return findings ordered by severity or decision
   impact.

## Output Shape

Lead with findings. Include file references when available, transcript path, and
test or evidence gaps.
