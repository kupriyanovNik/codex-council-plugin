# Storage Policy

Default storage is workspace-local:

```text
<workspace>/.codex-council/
  council.sqlite
  artifacts/<session-id>/
  exports/<session-id>-transcript.md
```

The plugin installation directory stores code and skill instructions only. It
does not store council sessions by default.

## Retention

Keep session data until the user deletes it. Use `export_transcript` before
manual cleanup if the result needs to be preserved outside the workspace.

## Privacy

Artifacts may contain source code, logs, design notes, or review findings. Treat
`.codex-council/` as project data. Do not commit it unless a project explicitly
wants council records in version control.

## Portability

SQLite is the local production backend. The MCP tool contract intentionally uses
workspace root and session id parameters so a future remote backend can keep the
same agent-facing workflow.

