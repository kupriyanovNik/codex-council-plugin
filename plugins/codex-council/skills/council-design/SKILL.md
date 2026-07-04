---
name: council-design
description: Run a Codex Council design session for architecture, system design, product tradeoffs, implementation strategy, or protocol design using role-based subagents and the local MCP blackboard.
---

# Council Design

Use this skill for design decisions before implementation.

## Required References

Read `../../docs/protocol.md`, `../../docs/roles.md`, and
`../../docs/storage-policy.md`. Read `../../docs/subagent-prompts.md` before
spawning subagents.

## Workflow

1. Create a council session with `mode: design` and `allow_writes: false`.
2. Use roles: Architect, Skeptic, Verifier. Add Security for sensitive systems.
3. Have Architect post the strongest design proposal.
4. Have Skeptic post failure modes and alternatives.
5. Have Verifier check local constraints, docs, and repo evidence.
6. Propose a decision and collect votes when useful.
7. Export transcript and return the recommended design plus rejected options.

## Output Shape

Use sections: recommendation, architecture, tradeoffs, risks, evidence, next
steps.
