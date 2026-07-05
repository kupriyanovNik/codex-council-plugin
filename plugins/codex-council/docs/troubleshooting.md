# Troubleshooting

## Subagents cannot discover `mcp__codex_council`

Expected path:

1. The parent starts a council session.
2. Each subagent receives the Codex Council prompt template.
3. The subagent calls `tool_search` with query `codex-council`.
4. The subagent uses the typed `mcp__codex_council.*` tools.

If `tool_search` returns unrelated tools or no `mcp__codex_council` tools, do
not fall back to shell, Python, sqlite3, or direct stdio calls to
`mcp/council_server.py` for normal council work.

Common cause during local plugin development: the current Codex thread started
before the plugin was installed or reinstalled. The parent may still have an old
tool registry, while newly spawned subagents may search a stale or incomplete
dynamic tool index.

Fix:

1. Confirm the installed plugin version with `codex plugin list`.
2. Start a fresh Codex thread after reinstalling the plugin.
3. Run the council again with the standard subagent prompt template.

Direct stdio calls are acceptable only for explicit diagnostics of the MCP
server itself. They are not the production transport for subagent councils.

## Legacy sessions after token-gated writer mode

Plugin versions before token-gated writer mode created sessions without a
registration token. If an old active `implement` session must be continued,
call `rotate_registration_token` for that session. For legacy sessions with no
stored token hash, this returns a new recovery `registration_token`.

For sessions that already have a token hash, `rotate_registration_token`
requires the current registration token before it returns a replacement.

Keep the returned token private in the parent. Do not pass it to subagents or
post it in Council messages, artifacts, transcripts, commits, or final
user-facing summaries.
