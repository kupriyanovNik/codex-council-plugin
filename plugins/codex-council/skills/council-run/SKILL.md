---
name: council-run
description: Run a generic Codex Council workflow with role-based subagents using the bundled local MCP blackboard. Use when the user asks for a council, mesh, multi-agent discussion, subagent debate, role-based review, design, decision support, research-style analysis, optional implementation, or an llm-council-like workflow inside Codex.
---

# Council Run

Use the local `codex-council` MCP server as the transport and state store. Do
not relay long agent outputs through the parent conversation.

## Required References

Read these files before starting a council:

- `../../docs/protocol.md`
- `../../docs/roles.md`
- `../../docs/storage-policy.md`
- `../../docs/subagent-prompts.md`

## Workflow

1. Select mode:
   - `light`: Architect and Skeptic for a quick second opinion.
   - `standard`: Architect, Skeptic, Verifier for most discussions.
   - `deep`: Architect, Skeptic, Verifier, Reviewer. Add Security only when
     safety, privacy, permission, or sensitive-data risks are relevant.
   - `review`: Reviewer, Skeptic, Verifier for read-only critique.
   - `design`: Architect, Skeptic, Verifier for plans, systems, product
     choices, protocols, or implementation strategy.
   - `implement`: Writer plus review roles, only when the user explicitly
     authorized file changes.
2. Call `create_session` with the current session workspace root, objective,
   mode, and `allow_writes`.
3. Spawn fresh subagents for each role using the relevant template from
   `subagent-prompts.md`.
4. Require each subagent to register through `register_agent`.
5. Register the parent as `chair` before it writes to MCP directly.
6. Run independent, cross-review, evidence, and decision rounds through MCP
   messages and artifacts.
7. Export a transcript with `export_transcript`.
8. Return a concise final synthesis with transcript path and any remaining
   uncertainty.

## Parent Rules

- Keep the parent context small. Store long content as artifacts.
- Use subagent final responses only as status beacons.
- Treat MCP state as the source of truth.
- Do not spawn Writer in read-only discussion, review, or design tasks.
