---
name: council-design
description: Run a Codex Council design session for plans, product tradeoffs, architecture, system design, research framing, implementation strategy, or protocol design using role-based subagents and the local MCP blackboard.
---

# Council Design

Use this skill for shaping a direction before committing to it. The output may
be a product decision, research plan, architecture proposal, process design, or
implementation strategy.

## Required References

Read `../../docs/protocol.md`, `../../docs/roles.md`, and
`../../docs/storage-policy.md`. Read `../../docs/subagent-prompts.md` before
spawning subagents.

## Workflow

1. Create a council session with `mode: design` and `allow_writes: false`.
2. Use roles: Architect, Skeptic, Verifier. Add Security only when safety,
   privacy, permission, or sensitive-data risks matter.
3. Have Architect post the strongest proposal or plan.
4. Have Skeptic post failure modes and alternatives.
5. Have Verifier check the task context, provided material, docs, local files,
   commands, or other evidence when available.
6. Propose a decision and collect votes when useful.
7. Export transcript and return the recommended design plus rejected options.

## Output Shape

Use sections that fit the task. Prefer recommendation, options, tradeoffs,
risks, evidence, and next steps.
