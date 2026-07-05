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
- `../../docs/troubleshooting.md`

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
   mode, and `allow_writes`. Keep the returned `registration_token` private.
3. If a privileged subagent is needed (`chair`, `writer`, or explicit `write`
   capability), register that exact `agent_id`, role, and capabilities from the
   parent with the private `registration_token`, then pass only the returned
   per-agent `agent_token` directly to that subagent.
4. Spawn fresh subagents for each role using the relevant template from
   `subagent-prompts.md`. Include the installed Codex Council plugin mention in
   the subagent input when the host supports structured mentions.
5. Require each subagent to call `tool_search` with query `codex-council` as
   its first tool step, then use the typed `mcp__codex_council.*` tools. If a
   subagent cannot discover those typed tools, treat it as blocked and do not
   let it fall back to shell, Python, sqlite3, or direct stdio calls. For local
   plugin development, this commonly means the current thread predates a plugin
   reinstall; start a fresh thread before retrying.
6. Require non-privileged subagents to register through
   `mcp__codex_council.register_agent`; privileged subagents should use the
   parent-provided `agent_token` for privileged task tools instead of receiving
   the session registration token. They must include that `agent_token` on every
   mutating Council call made as their own identity, including messages,
   acknowledgements, artifacts, claims, decisions, heartbeat, and task tools.
   If they repeat `register_agent` for their pre-registered privileged identity,
   they must include that same `agent_token`.
7. Register the parent as `chair` before it writes to MCP directly.
8. Run independent, cross-review, evidence, and decision rounds through MCP
   messages and artifacts.
9. Export a transcript with `export_transcript`.
10. Return a concise final synthesis with transcript path and any remaining
   uncertainty.

## Parent Rules

- Keep the parent context small. Store long content as artifacts.
- Use subagent final responses only as status beacons.
- Treat MCP state as the source of truth.
- Do not instruct subagents to use stdio fallback for normal council work.
- Do not spawn Writer in read-only discussion, review, or design tasks.
- Do not post registration tokens or agent tokens to Council messages,
  artifacts, transcripts, or the final answer.
- Never pass the session-wide `registration_token` to a subagent.
- Close, cancel, or archive sessions only from the parent with the private
  `registration_token` or from a Chair identity with its private `agent_token`.
