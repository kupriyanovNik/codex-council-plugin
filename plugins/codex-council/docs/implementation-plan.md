# Codex Council Implementation Plan

> For agentic workers: REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build a local Codex plugin that coordinates role-based subagents through a durable MCP blackboard.

**Architecture:** The plugin packages Codex skills plus a Python stdlib MCP server. Skills orchestrate subagents and the MCP server owns local state in SQLite plus workspace artifacts.

**Tech Stack:** Codex plugin manifest, Codex skills, MCP over stdio, Python 3 stdlib, SQLite.

**Local Root:** `/Users/nikitakuprianov/Downloads/codex-council-plugin`.

---

### Task 1: Plugin Scaffold

**Files:**
- Create: `.codex-plugin/plugin.json`
- Create: `.mcp.json`
- Create: `.agents/plugins/marketplace.json`

- [x] Scaffold `codex-council` under `/Users/nikitakuprianov/Downloads/codex-council-plugin/plugins`.
- [x] Add marketplace entry at `/Users/nikitakuprianov/Downloads/codex-council-plugin/.agents/plugins/marketplace.json`.
- [x] Declare the bundled MCP server in `.mcp.json`.

### Task 2: MCP Blackboard Server

**Files:**
- Create: `mcp/council_server.py`
- Create: `tests/test_council_server.py`

- [x] Implement MCP initialize, tools/list, and tools/call over stdio.
- [x] Implement SQLite-backed sessions, agents, messages, acks, artifacts, tasks, claims, decisions, votes, transcript export.
- [x] Add unit tests for message/artifact round trip, task leases, stdio tools/list, artifact ownership, cross-session isolation, workspace-root validation, registration, session lifecycle, and completion artifacts.

### Task 3: Skills And Protocol

**Files:**
- Create: `skills/council-run/SKILL.md`
- Create: `skills/council-review/SKILL.md`
- Create: `skills/council-design/SKILL.md`
- Create: `skills/council-implement/SKILL.md`
- Create: `docs/protocol.md`
- Create: `docs/roles.md`
- Create: `docs/storage-policy.md`

- [x] Keep skill bodies concise.
- [x] Move detailed protocol into docs for progressive disclosure.
- [x] Make writer role opt-in only.

### Task 4: Validation

**Files:**
- Create: `scripts/doctor.py`

- [x] Run MCP self-test.
- [x] Run Python unit tests.
- [x] Run skill validation.
- [x] Run plugin validation.
- [x] Confirm marketplace install through `codex plugin list`.
