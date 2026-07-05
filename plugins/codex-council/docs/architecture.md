# Codex Council Architecture

Codex Council is a local Codex plugin for role-based subagent workflows. It is
not a standalone product and does not call external LLM providers. Codex remains
the execution environment; the plugin adds durable coordination primitives.

## Components

- Skills define the workflows users invoke: general discussion, review, design,
  write-capable work, and the general council runner.
- The MCP server is the durable coordination layer. Agents use it to exchange
  short messages, store long artifacts, claim tasks, append claims, and vote on
  decisions.
- SQLite stores structured session state in the target workspace under
  `.codex-council/council.sqlite`.
- Artifact files store long Markdown, source notes, review notes, logs, test
  evidence when relevant, patches when writing, and transcripts under
  `.codex-council/artifacts/`.

## Data Flow

1. A council skill creates a session through `create_session`.
2. The parent agent spawns role-specific subagents and gives each one the
   workspace root, session id, role, mode, and protocol summary.
3. Each subagent registers itself through `register_agent`.
4. Agents use `post_message`, `list_messages`, `ack_message`, and artifact tools
   instead of relaying large content through the parent prompt.
5. Claims and decisions are written to the MCP server so the final answer can be
   reconstructed from durable evidence.
6. The parent exports a Markdown transcript with `export_transcript` before
   closing the session.

## Locality

All data stays local by default. The plugin writes only to the user-selected
workspace and the plugin installation directory. The MCP server uses Python
stdlib and SQLite; no package install step is required.

## Scaling Path

The current production-local backend is SQLite. A future team version can keep
the same tool contract and replace the storage adapter with Postgres and object
storage.
