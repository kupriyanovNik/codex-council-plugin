# Storage Policy

Default storage is workspace-local:

```text
<workspace>/.codex-council/
  council.sqlite
  artifacts/<session-id>/
  exports/<session-id>-transcript.md
```

The plugin installation directory stores plugin code and skill instructions
only. It does not store council sessions by default.

## Retention

Keep session data until the user deletes it. Use `export_transcript` before
manual cleanup if the result needs to be preserved outside the workspace.

## Privacy

Artifacts may contain source material, source code, logs, design notes, review
findings, reasoning drafts, or local context. Treat `.codex-council/` as project
data. Do not commit it unless a project explicitly wants council records in
version control.

Council storage is a shared workspace-local blackboard. Message `to_agents`
values route inboxes and reduce noise; they are not an access-control layer.
Assume every registered actor in a session, plus the coordinating parent, can
read session artifacts and message summaries. Keep secrets that should not be
visible to other roles out of Council artifacts.

Registration tokens and agent tokens are control-plane credentials. They should
be passed only as direct MCP tool arguments and must not be stored in artifacts,
messages, transcripts, commits, or user-facing summaries.

The server rejects symlinked Council storage directories and SQLite state files
(`council.sqlite` plus SQLite journal/WAL sidecars). Council state must resolve
inside `<workspace>/.codex-council/`.

## Portability

SQLite is the local production backend. The MCP tool contract intentionally uses
workspace root and session id parameters so a future remote backend can keep the
same agent-facing workflow.
