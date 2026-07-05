#!/usr/bin/env python3
from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import hashlib
import json
import pathlib
import re
import secrets
import sqlite3
import sys
import traceback
import uuid
from typing import Any, Callable, Iterator


SERVER_NAME = "codex-council"
SERVER_VERSION = "0.1.1"
PROTOCOL_VERSION = "2024-11-05"
IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
PRIVILEGED_ROLES = {"chair", "writer"}
WRITE_CAPABILITIES = {"write", "writer"}
SQLITE_STATE_FILES = (
    "council.sqlite",
    "council.sqlite-wal",
    "council.sqlite-shm",
    "council.sqlite-journal",
)


INSTRUCTIONS = """Codex Council is a local blackboard for Codex subagents.
Use create_session first. Agents must register, exchange short messages through
post_message/list_messages, store long content as artifacts, ack messages after
reading, and use tasks/leases for write-capable work. Writer roles must only be
used when the user explicitly authorized file changes."""


JsonDict = dict[str, Any]


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def utc_timestamp() -> float:
    return dt.datetime.now(dt.timezone.utc).timestamp()


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:16]}"


def new_token() -> str:
    return secrets.token_urlsafe(32)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def as_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def from_json(value: str | None, default: Any) -> Any:
    if not value:
        return default
    return json.loads(value)


def require_string(args: JsonDict, key: str) -> str:
    value = args.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} must be a non-empty string")
    return value.strip()


def validate_identifier(value: str, key: str) -> str:
    if not IDENTIFIER_RE.fullmatch(value) or ".." in value:
        raise ValueError(
            f"{key} must be a simple identifier using letters, numbers, '.', '_', or '-'"
        )
    return value


def optional_string(args: JsonDict, key: str, default: str = "") -> str:
    value = args.get(key, default)
    if value is None:
        return default
    if not isinstance(value, str):
        raise ValueError(f"{key} must be a string")
    return value


def optional_bool(args: JsonDict, key: str, default: bool = False) -> bool:
    value = args.get(key, default)
    if not isinstance(value, bool):
        raise ValueError(f"{key} must be a boolean")
    return value


def optional_int(args: JsonDict, key: str, default: int) -> int:
    value = args.get(key, default)
    if not isinstance(value, int):
        raise ValueError(f"{key} must be an integer")
    return value


def optional_string_list(args: JsonDict, key: str, default: list[str] | None = None) -> list[str]:
    value = args.get(key, default if default is not None else [])
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"{key} must be a list of strings")
    return value


def workspace_root(args: JsonDict) -> pathlib.Path:
    raw = require_string(args, "workspace_root")
    root = pathlib.Path(raw).expanduser().resolve()
    if not root.exists():
        raise ValueError(f"workspace_root does not exist: {root}")
    if not root.is_dir():
        raise ValueError(f"workspace_root must be a directory: {root}")
    return root


def ensure_real_directory(path: pathlib.Path, label: str, *, within: pathlib.Path | None = None) -> pathlib.Path:
    if path.is_symlink():
        raise ValueError(f"{label} must not be a symlink: {path}")
    if path.exists() and not path.is_dir():
        raise ValueError(f"{label} must be a directory: {path}")
    path.mkdir(parents=True, exist_ok=True)
    resolved = path.resolve()
    if within is not None and resolved != within.resolve() and not resolved.is_relative_to(within.resolve()):
        raise ValueError(f"{label} escapes expected directory: {path}")
    return path


def council_root(root: pathlib.Path) -> pathlib.Path:
    path = root / ".codex-council"
    return ensure_real_directory(path, "council directory", within=root)


def safe_child_path(parent: pathlib.Path, child: str, label: str) -> pathlib.Path:
    base = parent.resolve()
    candidate = base / child
    if candidate.is_symlink():
        raise ValueError(f"{label} must not be a symlink: {child}")
    path = candidate.resolve()
    if path != base and not path.is_relative_to(base):
        raise ValueError(f"{label} escapes expected directory: {child}")
    return path


def ensure_safe_state_file(parent: pathlib.Path, filename: str, label: str) -> pathlib.Path:
    base = parent.resolve()
    candidate = parent / filename
    if candidate.is_symlink():
        raise ValueError(f"{label} must not be a symlink: {candidate}")
    if candidate.exists() and not candidate.is_file():
        raise ValueError(f"{label} must be a file: {candidate}")
    resolved = candidate.resolve()
    if resolved != base and not resolved.is_relative_to(base):
        raise ValueError(f"{label} escapes expected directory: {candidate}")
    return candidate


def db_path(root: pathlib.Path) -> pathlib.Path:
    council = council_root(root)
    for filename in SQLITE_STATE_FILES:
        ensure_safe_state_file(council, filename, "SQLite state file")
    return council / "council.sqlite"


def artifact_root(root: pathlib.Path, session_id: str) -> pathlib.Path:
    validate_identifier(session_id, "session_id")
    artifacts = ensure_real_directory(council_root(root) / "artifacts", "artifact root", within=council_root(root))
    path = safe_child_path(artifacts, session_id, "session_id")
    return ensure_real_directory(path, "session artifact directory", within=artifacts)


def export_root(root: pathlib.Path) -> pathlib.Path:
    path = council_root(root) / "exports"
    return ensure_real_directory(path, "export root", within=council_root(root))


@contextlib.contextmanager
def connect(root: pathlib.Path) -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(db_path(root))
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        ensure_schema(conn)
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS sessions (
          id TEXT PRIMARY KEY,
          workspace_root TEXT NOT NULL,
          objective TEXT NOT NULL,
          mode TEXT NOT NULL,
          allow_writes INTEGER NOT NULL DEFAULT 0,
          registration_token_hash TEXT,
          status TEXT NOT NULL DEFAULT 'active',
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS agents (
          session_id TEXT NOT NULL,
          agent_id TEXT NOT NULL,
          role TEXT NOT NULL,
          capabilities_json TEXT NOT NULL DEFAULT '[]',
          agent_token_hash TEXT,
          registered_at TEXT NOT NULL,
          heartbeat_at TEXT NOT NULL,
          PRIMARY KEY (session_id, agent_id),
          FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS messages (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          session_id TEXT NOT NULL,
          from_agent TEXT NOT NULL,
          to_agents_json TEXT NOT NULL,
          topic TEXT NOT NULL,
          kind TEXT NOT NULL,
          summary TEXT NOT NULL,
          artifact_id TEXT,
          requires_response INTEGER NOT NULL DEFAULT 0,
          created_at TEXT NOT NULL,
          FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS message_acks (
          session_id TEXT NOT NULL,
          message_id INTEGER NOT NULL,
          agent_id TEXT NOT NULL,
          acked_at TEXT NOT NULL,
          PRIMARY KEY (session_id, message_id, agent_id),
          FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE,
          FOREIGN KEY (message_id) REFERENCES messages(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS artifacts (
          id TEXT PRIMARY KEY,
          session_id TEXT NOT NULL,
          title TEXT NOT NULL,
          kind TEXT NOT NULL,
          rel_path TEXT NOT NULL,
          created_by TEXT NOT NULL,
          sha256 TEXT NOT NULL,
          bytes INTEGER NOT NULL,
          created_at TEXT NOT NULL,
          FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS tasks (
          id TEXT PRIMARY KEY,
          session_id TEXT NOT NULL,
          title TEXT NOT NULL,
          description TEXT NOT NULL,
          status TEXT NOT NULL,
          created_by TEXT NOT NULL,
          claimed_by TEXT,
          lease_expires_at REAL,
          artifact_id TEXT,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL,
          FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS claims (
          id TEXT PRIMARY KEY,
          session_id TEXT NOT NULL,
          from_agent TEXT NOT NULL,
          statement TEXT NOT NULL,
          confidence TEXT NOT NULL,
          evidence_refs_json TEXT NOT NULL,
          created_at TEXT NOT NULL,
          FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS decisions (
          id TEXT PRIMARY KEY,
          session_id TEXT NOT NULL,
          title TEXT NOT NULL,
          status TEXT NOT NULL,
          proposed_by TEXT NOT NULL,
          rationale_artifact_id TEXT,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL,
          FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS decision_votes (
          decision_id TEXT NOT NULL,
          session_id TEXT NOT NULL,
          agent_id TEXT NOT NULL,
          stance TEXT NOT NULL,
          rationale TEXT NOT NULL,
          created_at TEXT NOT NULL,
          PRIMARY KEY (decision_id, agent_id),
          FOREIGN KEY (decision_id) REFERENCES decisions(id) ON DELETE CASCADE,
          FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_messages_session_id ON messages(session_id, id);
        CREATE INDEX IF NOT EXISTS idx_tasks_session_status ON tasks(session_id, status);
        CREATE INDEX IF NOT EXISTS idx_claims_session ON claims(session_id, created_at);
        """
    )
    task_columns = {row["name"] for row in conn.execute("PRAGMA table_info(tasks)").fetchall()}
    if "created_by" not in task_columns:
        conn.execute("ALTER TABLE tasks ADD COLUMN created_by TEXT")
    session_columns = {row["name"] for row in conn.execute("PRAGMA table_info(sessions)").fetchall()}
    if "registration_token_hash" not in session_columns:
        conn.execute("ALTER TABLE sessions ADD COLUMN registration_token_hash TEXT")
    agent_columns = {row["name"] for row in conn.execute("PRAGMA table_info(agents)").fetchall()}
    if "agent_token_hash" not in agent_columns:
        conn.execute("ALTER TABLE agents ADD COLUMN agent_token_hash TEXT")


def ensure_session(conn: sqlite3.Connection, session_id: str) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
    if row is None:
        raise ValueError(f"unknown session_id: {session_id}")
    return row


def ensure_active_session(session: sqlite3.Row) -> None:
    if session["status"] != "active":
        raise ValueError(f"session is not active: {session['status']}")


def ensure_writes_allowed(session: sqlite3.Row) -> None:
    if not bool(session["allow_writes"]):
        raise ValueError("session does not allow write-capable tasks")
    if session["mode"] != "implement":
        raise ValueError("write-capable tasks require implement mode")


def ensure_agent_registered(conn: sqlite3.Connection, session_id: str, agent_id: str) -> sqlite3.Row:
    row = conn.execute(
        "SELECT * FROM agents WHERE session_id = ? AND agent_id = ?",
        (session_id, agent_id),
    ).fetchone()
    if row is None:
        raise ValueError(f"unknown agent_id for session {session_id}: {agent_id}")
    return row


def token_matches(stored_hash: str | None, token: str) -> bool:
    return bool(stored_hash and token and secrets.compare_digest(stored_hash, token_hash(token)))


def agent_capabilities(agent: sqlite3.Row) -> set[str]:
    return {
        item.casefold()
        for item in from_json(agent["capabilities_json"], [])
        if isinstance(item, str)
    }


def agent_role(agent: sqlite3.Row) -> str:
    return str(agent["role"]).strip().casefold()


def is_privileged_registration(role: str, capabilities: list[str]) -> bool:
    caps = {item.casefold() for item in capabilities}
    return role.strip().casefold() in PRIVILEGED_ROLES or not caps.isdisjoint(WRITE_CAPABILITIES)


def require_registration_token(session: sqlite3.Row, token: str) -> None:
    if not token_matches(session["registration_token_hash"], token):
        raise ValueError("valid registration_token is required for privileged agent registration")


def require_agent_token(agent: sqlite3.Row, token: str) -> None:
    if not token_matches(agent["agent_token_hash"], token):
        raise ValueError(f"valid agent_token is required for privileged agent: {agent['agent_id']}")


def ensure_agent_identity(
    conn: sqlite3.Connection,
    session_id: str,
    agent_id: str,
    agent_token: str = "",
) -> sqlite3.Row:
    agent = ensure_agent_registered(conn, session_id, agent_id)
    if agent["agent_token_hash"]:
        require_agent_token(agent, agent_token)
    return agent


def ensure_task_creator(conn: sqlite3.Connection, session_id: str, agent_id: str, agent_token: str) -> None:
    agent = ensure_agent_registered(conn, session_id, agent_id)
    caps = agent_capabilities(agent)
    if agent_role(agent) not in {"chair", "writer"} and caps.isdisjoint(WRITE_CAPABILITIES):
        raise ValueError(f"agent is not allowed to create write tasks: {agent_id}")
    require_agent_token(agent, agent_token)


def ensure_writer_agent(conn: sqlite3.Connection, session_id: str, agent_id: str, agent_token: str) -> None:
    agent = ensure_agent_registered(conn, session_id, agent_id)
    caps = agent_capabilities(agent)
    if agent_role(agent) != "writer" and caps.isdisjoint(WRITE_CAPABILITIES):
        raise ValueError(f"agent is not allowed to claim or complete write tasks: {agent_id}")
    require_agent_token(agent, agent_token)


def ensure_chair_agent(conn: sqlite3.Connection, session_id: str, agent_id: str, agent_token: str) -> None:
    agent = ensure_agent_registered(conn, session_id, agent_id)
    if agent_role(agent) != "chair":
        raise ValueError(f"agent is not allowed to close sessions: {agent_id}")
    require_agent_token(agent, agent_token)


def ensure_target_agents_registered(conn: sqlite3.Connection, session_id: str, agent_ids: list[str]) -> None:
    for agent_id in agent_ids:
        if agent_id not in {"all", "*"}:
            ensure_agent_registered(conn, session_id, agent_id)


def ensure_artifact_in_session(conn: sqlite3.Connection, session_id: str, artifact_id: str | None) -> sqlite3.Row | None:
    if not artifact_id:
        return None
    row = conn.execute(
        "SELECT * FROM artifacts WHERE session_id = ? AND id = ?",
        (session_id, artifact_id),
    ).fetchone()
    if row is None:
        raise ValueError(f"unknown artifact_id for session {session_id}: {artifact_id}")
    return row


def ensure_artifact_refs_in_session(conn: sqlite3.Connection, session_id: str, refs: list[str]) -> None:
    for ref in refs:
        if ref.startswith("art-"):
            ensure_artifact_in_session(conn, session_id, ref)


def ensure_message_in_session(conn: sqlite3.Connection, session_id: str, message_id: int) -> None:
    row = conn.execute(
        "SELECT 1 FROM messages WHERE session_id = ? AND id = ?",
        (session_id, message_id),
    ).fetchone()
    if row is None:
        raise ValueError(f"unknown message_id for session {session_id}: {message_id}")


def ensure_decision_in_session(conn: sqlite3.Connection, session_id: str, decision_id: str) -> None:
    row = conn.execute(
        "SELECT 1 FROM decisions WHERE session_id = ? AND id = ?",
        (session_id, decision_id),
    ).fetchone()
    if row is None:
        raise ValueError(f"unknown decision_id for session {session_id}: {decision_id}")


def store_artifact(
    conn: sqlite3.Connection,
    root: pathlib.Path,
    session_id: str,
    *,
    title: str,
    kind: str,
    created_by: str,
    content: str,
) -> JsonDict:
    artifact_id = new_id("art")
    suffix = ".md" if kind in {"markdown", "analysis", "review", "decision"} else ".txt"
    path = artifact_root(root, session_id) / f"{artifact_id}{suffix}"
    encoded = content.encode("utf-8")
    path.write_bytes(encoded)
    digest = hashlib.sha256(encoded).hexdigest()
    rel_path = str(path.relative_to(council_root(root)))
    conn.execute(
        """
        INSERT INTO artifacts
        (id, session_id, title, kind, rel_path, created_by, sha256, bytes, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (artifact_id, session_id, title, kind, rel_path, created_by, digest, len(encoded), utc_now()),
    )
    return {
        "artifact_id": artifact_id,
        "path": str(path),
        "rel_path": rel_path,
        "sha256": digest,
        "bytes": len(encoded),
    }


def tool_create_session(args: JsonDict) -> JsonDict:
    root = workspace_root(args)
    objective = require_string(args, "objective")
    mode = optional_string(args, "mode", "standard")
    if mode not in {"light", "standard", "deep", "review", "design", "implement"}:
        raise ValueError("mode must be light, standard, deep, review, design, or implement")
    allow_writes = optional_bool(args, "allow_writes", False)
    if allow_writes and mode != "implement":
        raise ValueError("allow_writes requires mode: implement")
    session_id = args.get("session_id") or new_id("session")
    if not isinstance(session_id, str):
        raise ValueError("session_id must be a string")
    session_id = validate_identifier(session_id.strip(), "session_id")
    registration_token = new_token()
    now = utc_now()
    with connect(root) as conn:
        artifact_dir = artifact_root(root, session_id)
        conn.execute(
            """
            INSERT INTO sessions
            (id, workspace_root, objective, mode, allow_writes, registration_token_hash,
             status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, 'active', ?, ?)
            """,
            (session_id, str(root), objective, mode, int(allow_writes), token_hash(registration_token), now, now),
        )
    return {
        "session_id": session_id,
        "workspace_root": str(root),
        "council_dir": str(council_root(root)),
        "db_path": str(db_path(root)),
        "artifact_dir": str(artifact_dir),
        "allow_writes": allow_writes,
        "mode": mode,
        "registration_token": registration_token,
    }


def tool_register_agent(args: JsonDict) -> JsonDict:
    root = workspace_root(args)
    session_id = require_string(args, "session_id")
    agent_id = require_string(args, "agent_id")
    role = require_string(args, "role")
    capabilities = optional_string_list(args, "capabilities")
    registration_token = optional_string(args, "registration_token", "")
    existing_agent_token = optional_string(args, "agent_token", "")
    now = utc_now()
    agent_token: str | None = None
    with connect(root) as conn:
        conn.execute("BEGIN IMMEDIATE")
        session = ensure_session(conn, session_id)
        ensure_active_session(session)
        existing = conn.execute(
            "SELECT * FROM agents WHERE session_id = ? AND agent_id = ?",
            (session_id, agent_id),
        ).fetchone()
        requested_privileged = is_privileged_registration(role, capabilities)
        if existing is not None:
            existing_capabilities = from_json(existing["capabilities_json"], [])
            metadata_changed = existing["role"] != role or existing_capabilities != capabilities
            if metadata_changed:
                raise ValueError("agent role/capabilities are immutable; register a new agent_id")
            elif requested_privileged and not existing["agent_token_hash"]:
                require_registration_token(session, registration_token)
                agent_token = new_token()
            elif existing["agent_token_hash"]:
                require_agent_token(existing, existing_agent_token)
        elif requested_privileged:
            require_registration_token(session, registration_token)
            agent_token = new_token()
        conn.execute(
            """
            INSERT INTO agents
            (session_id, agent_id, role, capabilities_json, agent_token_hash, registered_at, heartbeat_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(session_id, agent_id) DO UPDATE SET
              role = excluded.role,
              capabilities_json = excluded.capabilities_json,
              agent_token_hash = COALESCE(excluded.agent_token_hash, agents.agent_token_hash),
              heartbeat_at = excluded.heartbeat_at
            """,
            (
                session_id,
                agent_id,
                role,
                as_json(capabilities),
                token_hash(agent_token) if agent_token else None,
                now,
                now,
            ),
        )
    result = {"registered": True, "session_id": session_id, "agent_id": agent_id, "role": role}
    if agent_token:
        result["agent_token"] = agent_token
    return result


def tool_rotate_registration_token(args: JsonDict) -> JsonDict:
    root = workspace_root(args)
    session_id = require_string(args, "session_id")
    current_registration_token = optional_string(args, "current_registration_token", "")
    rotated_token = new_token()
    now = utc_now()
    with connect(root) as conn:
        session = ensure_session(conn, session_id)
        ensure_active_session(session)
        existing_hash = session["registration_token_hash"]
        if existing_hash and not token_matches(existing_hash, current_registration_token):
            raise ValueError("valid current_registration_token is required to rotate registration token")
        conn.execute(
            "UPDATE sessions SET registration_token_hash = ?, updated_at = ? WHERE id = ?",
            (token_hash(rotated_token), now, session_id),
        )
    return {
        "session_id": session_id,
        "registration_token": rotated_token,
        "rotated": True,
        "legacy_recovery": not bool(existing_hash),
    }


def tool_heartbeat_agent(args: JsonDict) -> JsonDict:
    root = workspace_root(args)
    session_id = require_string(args, "session_id")
    agent_id = require_string(args, "agent_id")
    agent_token = optional_string(args, "agent_token", "")
    now = utc_now()
    with connect(root) as conn:
        session = ensure_session(conn, session_id)
        ensure_active_session(session)
        ensure_agent_identity(conn, session_id, agent_id, agent_token)
        updated = conn.execute(
            "UPDATE agents SET heartbeat_at = ? WHERE session_id = ? AND agent_id = ?",
            (now, session_id, agent_id),
        ).rowcount
    return {"updated": updated == 1, "heartbeat_at": now}


def tool_put_artifact(args: JsonDict) -> JsonDict:
    root = workspace_root(args)
    session_id = require_string(args, "session_id")
    title = require_string(args, "title")
    created_by = require_string(args, "created_by")
    content = require_string(args, "content")
    agent_token = optional_string(args, "agent_token", "")
    kind = optional_string(args, "kind", "markdown")
    with connect(root) as conn:
        session = ensure_session(conn, session_id)
        ensure_active_session(session)
        ensure_agent_identity(conn, session_id, created_by, agent_token)
        artifact = store_artifact(
            conn,
            root,
            session_id,
            title=title,
            kind=kind,
            created_by=created_by,
            content=content,
        )
    return artifact


def tool_get_artifact(args: JsonDict) -> JsonDict:
    root = workspace_root(args)
    session_id = require_string(args, "session_id")
    artifact_id = require_string(args, "artifact_id")
    agent_id = optional_string(args, "agent_id", "") or None
    with connect(root) as conn:
        ensure_session(conn, session_id)
        if agent_id:
            ensure_agent_registered(conn, session_id, agent_id)
        row = conn.execute(
            "SELECT * FROM artifacts WHERE session_id = ? AND id = ?",
            (session_id, artifact_id),
        ).fetchone()
    if row is None:
        raise ValueError(f"unknown artifact_id: {artifact_id}")
    path = safe_child_path(council_root(root), row["rel_path"], "artifact path")
    return {
        "artifact_id": artifact_id,
        "title": row["title"],
        "kind": row["kind"],
        "path": str(path),
        "content": path.read_text(encoding="utf-8"),
        "sha256": row["sha256"],
    }


def tool_post_message(args: JsonDict) -> JsonDict:
    root = workspace_root(args)
    session_id = require_string(args, "session_id")
    from_agent = require_string(args, "from_agent")
    to_agents = optional_string_list(args, "to_agents", ["all"])
    topic = optional_string(args, "topic", "general")
    kind = optional_string(args, "kind", "message")
    summary = require_string(args, "summary")
    requires_response = optional_bool(args, "requires_response", False)
    artifact_id = optional_string(args, "artifact_id", "") or None
    artifact_content = args.get("artifact_content")
    agent_token = optional_string(args, "agent_token", "")
    with connect(root) as conn:
        session = ensure_session(conn, session_id)
        ensure_active_session(session)
        ensure_agent_identity(conn, session_id, from_agent, agent_token)
        ensure_target_agents_registered(conn, session_id, to_agents)
        if artifact_content is not None:
            if not isinstance(artifact_content, str):
                raise ValueError("artifact_content must be a string")
            artifact = store_artifact(
                conn,
                root,
                session_id,
                title=f"{kind}: {summary[:80]}",
                kind=kind,
                created_by=from_agent,
                content=artifact_content,
            )
            artifact_id = artifact["artifact_id"]
        else:
            ensure_artifact_in_session(conn, session_id, artifact_id)
        conn.execute(
            """
            INSERT INTO messages
            (session_id, from_agent, to_agents_json, topic, kind, summary, artifact_id,
             requires_response, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                session_id,
                from_agent,
                as_json(to_agents),
                topic,
                kind,
                summary,
                artifact_id,
                int(requires_response),
                utc_now(),
            ),
        )
        message_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    return {"message_id": message_id, "artifact_id": artifact_id, "to_agents": to_agents}


def addressed_to(to_agents: list[str], agent_id: str | None) -> bool:
    if agent_id is None:
        return True
    return agent_id in to_agents or "all" in to_agents or "*" in to_agents


def tool_list_messages(args: JsonDict) -> JsonDict:
    root = workspace_root(args)
    session_id = require_string(args, "session_id")
    agent_id = optional_string(args, "agent_id", "")
    agent_filter = agent_id or None
    topic = optional_string(args, "topic", "")
    since_message_id = optional_int(args, "since_message_id", 0)
    include_acked = optional_bool(args, "include_acked", False)
    with connect(root) as conn:
        ensure_session(conn, session_id)
        if agent_filter:
            ensure_agent_registered(conn, session_id, agent_filter)
        rows = conn.execute(
            """
            SELECT * FROM messages
            WHERE session_id = ? AND id > ?
            ORDER BY id ASC
            """,
            (session_id, since_message_id),
        ).fetchall()
        acked = set()
        if agent_filter and not include_acked:
            acked_rows = conn.execute(
                "SELECT message_id FROM message_acks WHERE session_id = ? AND agent_id = ?",
                (session_id, agent_filter),
            ).fetchall()
            acked = {row["message_id"] for row in acked_rows}
    messages = []
    for row in rows:
        to_agents = from_json(row["to_agents_json"], [])
        if topic and row["topic"] != topic:
            continue
        if not addressed_to(to_agents, agent_filter):
            continue
        if agent_filter and not include_acked and row["id"] in acked:
            continue
        messages.append(
            {
                "message_id": row["id"],
                "from_agent": row["from_agent"],
                "to_agents": to_agents,
                "topic": row["topic"],
                "kind": row["kind"],
                "summary": row["summary"],
                "artifact_id": row["artifact_id"],
                "requires_response": bool(row["requires_response"]),
                "created_at": row["created_at"],
            }
        )
    return {"messages": messages, "count": len(messages)}


def tool_ack_message(args: JsonDict) -> JsonDict:
    root = workspace_root(args)
    session_id = require_string(args, "session_id")
    agent_id = require_string(args, "agent_id")
    agent_token = optional_string(args, "agent_token", "")
    message_id = optional_int(args, "message_id", 0)
    if message_id <= 0:
        raise ValueError("message_id must be positive")
    now = utc_now()
    with connect(root) as conn:
        session = ensure_session(conn, session_id)
        ensure_active_session(session)
        ensure_agent_identity(conn, session_id, agent_id, agent_token)
        ensure_message_in_session(conn, session_id, message_id)
        conn.execute(
            """
            INSERT OR IGNORE INTO message_acks (session_id, message_id, agent_id, acked_at)
            VALUES (?, ?, ?, ?)
            """,
            (session_id, message_id, agent_id, now),
        )
    return {"acked": True, "message_id": message_id, "agent_id": agent_id, "acked_at": now}


def tool_create_task(args: JsonDict) -> JsonDict:
    root = workspace_root(args)
    session_id = require_string(args, "session_id")
    title = require_string(args, "title")
    description = require_string(args, "description")
    created_by = require_string(args, "created_by")
    agent_token = require_string(args, "agent_token")
    task_id = optional_string(args, "task_id", "") or new_id("task")
    artifact_id = optional_string(args, "artifact_id", "") or None
    now = utc_now()
    with connect(root) as conn:
        session = ensure_session(conn, session_id)
        ensure_active_session(session)
        ensure_writes_allowed(session)
        ensure_task_creator(conn, session_id, created_by, agent_token)
        ensure_artifact_in_session(conn, session_id, artifact_id)
        conn.execute(
            """
            INSERT INTO tasks
            (id, session_id, title, description, status, created_by, artifact_id, created_at, updated_at)
            VALUES (?, ?, ?, ?, 'open', ?, ?, ?, ?)
            """,
            (task_id, session_id, title, description, created_by, artifact_id, now, now),
        )
    return {"task_id": task_id, "status": "open", "created_by": created_by}


def tool_claim_task(args: JsonDict) -> JsonDict:
    root = workspace_root(args)
    session_id = require_string(args, "session_id")
    task_id = require_string(args, "task_id")
    agent_id = require_string(args, "agent_id")
    agent_token = require_string(args, "agent_token")
    lease_seconds = optional_int(args, "lease_seconds", 900)
    if lease_seconds <= 0:
        raise ValueError("lease_seconds must be positive")
    now_ts = utc_timestamp()
    lease_expires = now_ts + lease_seconds
    now = utc_now()
    with connect(root) as conn:
        session = ensure_session(conn, session_id)
        ensure_active_session(session)
        ensure_writes_allowed(session)
        ensure_writer_agent(conn, session_id, agent_id, agent_token)
        updated = conn.execute(
            """
            UPDATE tasks
            SET status = 'claimed', claimed_by = ?, lease_expires_at = ?, updated_at = ?
            WHERE session_id = ? AND id = ? AND (
              status IN ('open', 'released')
              OR (status = 'claimed' AND lease_expires_at IS NOT NULL AND lease_expires_at < ?)
              OR (status = 'claimed' AND claimed_by = ?)
            )
            """,
            (agent_id, lease_expires, now, session_id, task_id, now_ts, agent_id),
        ).rowcount
        if updated == 1:
            return {"claimed": True, "task_id": task_id, "claimed_by": agent_id, "lease_expires_at": lease_expires}
        row = conn.execute(
            "SELECT * FROM tasks WHERE session_id = ? AND id = ?",
            (session_id, task_id),
        ).fetchone()
        if row is None:
            raise ValueError(f"unknown task_id: {task_id}")
        return {
            "claimed": False,
            "task_id": task_id,
            "claimed_by": row["claimed_by"],
            "lease_expires_at": row["lease_expires_at"],
            "status": row["status"],
        }


def tool_complete_task(args: JsonDict) -> JsonDict:
    root = workspace_root(args)
    session_id = require_string(args, "session_id")
    task_id = require_string(args, "task_id")
    agent_id = require_string(args, "agent_id")
    agent_token = require_string(args, "agent_token")
    artifact_id = optional_string(args, "artifact_id", "") or None
    if artifact_id is None:
        raise ValueError("complete_task requires artifact_id")
    now_ts = utc_timestamp()
    now = utc_now()
    with connect(root) as conn:
        session = ensure_session(conn, session_id)
        ensure_active_session(session)
        ensure_writes_allowed(session)
        ensure_writer_agent(conn, session_id, agent_id, agent_token)
        artifact = ensure_artifact_in_session(conn, session_id, artifact_id)
        if artifact is not None and artifact["created_by"] != agent_id:
            raise ValueError(f"completion artifact was created by {artifact['created_by']}, not {agent_id}")
        updated = conn.execute(
            """
            UPDATE tasks
            SET status = 'done', claimed_by = ?, artifact_id = ?,
                lease_expires_at = NULL, updated_at = ?
            WHERE session_id = ? AND id = ?
              AND status = 'claimed'
              AND claimed_by = ?
              AND lease_expires_at IS NOT NULL
              AND lease_expires_at >= ?
            """,
            (agent_id, artifact_id, now, session_id, task_id, agent_id, now_ts),
        ).rowcount
        if updated == 1:
            return {"task_id": task_id, "status": "done", "completed_by": agent_id}
        row = conn.execute(
            "SELECT * FROM tasks WHERE session_id = ? AND id = ?",
            (session_id, task_id),
        ).fetchone()
        if row is None:
            raise ValueError(f"unknown task_id: {task_id}")
        if row["status"] != "claimed":
            raise ValueError(f"task must be claimed before completion: {task_id}")
        if row["claimed_by"] != agent_id:
            raise ValueError(f"task is claimed by {row['claimed_by']}, not {agent_id}")
        if row["lease_expires_at"] is None or row["lease_expires_at"] < now_ts:
            raise ValueError(f"task lease expired for {task_id}")
        raise ValueError(f"task could not be completed due to a concurrent update: {task_id}")


def tool_append_claim(args: JsonDict) -> JsonDict:
    root = workspace_root(args)
    session_id = require_string(args, "session_id")
    from_agent = require_string(args, "from_agent")
    statement = require_string(args, "statement")
    agent_token = optional_string(args, "agent_token", "")
    confidence = optional_string(args, "confidence", "medium")
    evidence_refs = optional_string_list(args, "evidence_refs")
    claim_id = optional_string(args, "claim_id", "") or new_id("claim")
    with connect(root) as conn:
        session = ensure_session(conn, session_id)
        ensure_active_session(session)
        ensure_agent_identity(conn, session_id, from_agent, agent_token)
        ensure_artifact_refs_in_session(conn, session_id, evidence_refs)
        conn.execute(
            """
            INSERT INTO claims
            (id, session_id, from_agent, statement, confidence, evidence_refs_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (claim_id, session_id, from_agent, statement, confidence, as_json(evidence_refs), utc_now()),
        )
    return {"claim_id": claim_id}


def tool_propose_decision(args: JsonDict) -> JsonDict:
    root = workspace_root(args)
    session_id = require_string(args, "session_id")
    title = require_string(args, "title")
    proposed_by = require_string(args, "proposed_by")
    agent_token = optional_string(args, "agent_token", "")
    rationale_artifact_id = optional_string(args, "rationale_artifact_id", "") or None
    decision_id = optional_string(args, "decision_id", "") or new_id("decision")
    now = utc_now()
    with connect(root) as conn:
        session = ensure_session(conn, session_id)
        ensure_active_session(session)
        ensure_agent_identity(conn, session_id, proposed_by, agent_token)
        ensure_artifact_in_session(conn, session_id, rationale_artifact_id)
        conn.execute(
            """
            INSERT INTO decisions
            (id, session_id, title, status, proposed_by, rationale_artifact_id, created_at, updated_at)
            VALUES (?, ?, ?, 'proposed', ?, ?, ?, ?)
            """,
            (decision_id, session_id, title, proposed_by, rationale_artifact_id, now, now),
        )
    return {"decision_id": decision_id, "status": "proposed"}


def tool_vote_decision(args: JsonDict) -> JsonDict:
    root = workspace_root(args)
    session_id = require_string(args, "session_id")
    decision_id = require_string(args, "decision_id")
    agent_id = require_string(args, "agent_id")
    agent_token = optional_string(args, "agent_token", "")
    stance = require_string(args, "stance")
    if stance not in {"approve", "reject", "abstain", "needs-work"}:
        raise ValueError("stance must be approve, reject, abstain, or needs-work")
    rationale = optional_string(args, "rationale", "")
    with connect(root) as conn:
        session = ensure_session(conn, session_id)
        ensure_active_session(session)
        ensure_agent_identity(conn, session_id, agent_id, agent_token)
        ensure_decision_in_session(conn, session_id, decision_id)
        conn.execute(
            """
            INSERT INTO decision_votes
            (decision_id, session_id, agent_id, stance, rationale, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(decision_id, agent_id) DO UPDATE SET
              stance = excluded.stance,
              rationale = excluded.rationale,
              created_at = excluded.created_at
            """,
            (decision_id, session_id, agent_id, stance, rationale, utc_now()),
        )
    return {"decision_id": decision_id, "agent_id": agent_id, "stance": stance}


def tool_get_session_state(args: JsonDict) -> JsonDict:
    root = workspace_root(args)
    session_id = require_string(args, "session_id")
    with connect(root) as conn:
        session = ensure_session(conn, session_id)
        counts = {}
        for table in ["agents", "messages", "artifacts", "tasks", "claims", "decisions"]:
            counts[table] = conn.execute(
                f"SELECT COUNT(*) FROM {table} WHERE session_id = ?",
                (session_id,),
            ).fetchone()[0]
        open_tasks = [
            dict(row)
            for row in conn.execute(
                "SELECT id, title, status, claimed_by, lease_expires_at FROM tasks WHERE session_id = ? AND status != 'done'",
                (session_id,),
            ).fetchall()
        ]
        agents = [
            {
                "agent_id": row["agent_id"],
                "role": row["role"],
                "capabilities": from_json(row["capabilities_json"], []),
                "heartbeat_at": row["heartbeat_at"],
            }
            for row in conn.execute(
                "SELECT * FROM agents WHERE session_id = ? ORDER BY agent_id",
                (session_id,),
            ).fetchall()
        ]
    session_dict = dict(session)
    session_dict["has_registration_token"] = bool(session_dict.pop("registration_token_hash", None))
    return {
        "session": session_dict,
        "counts": counts,
        "agents": agents,
        "open_tasks": open_tasks,
    }


def tool_export_transcript(args: JsonDict) -> JsonDict:
    root = workspace_root(args)
    session_id = require_string(args, "session_id")
    with connect(root) as conn:
        session = ensure_session(conn, session_id)
        messages = conn.execute(
            "SELECT * FROM messages WHERE session_id = ? ORDER BY id",
            (session_id,),
        ).fetchall()
        claims = conn.execute(
            "SELECT * FROM claims WHERE session_id = ? ORDER BY created_at",
            (session_id,),
        ).fetchall()
        decisions = conn.execute(
            "SELECT * FROM decisions WHERE session_id = ? ORDER BY created_at",
            (session_id,),
        ).fetchall()
        votes = conn.execute(
            "SELECT * FROM decision_votes WHERE session_id = ? ORDER BY created_at",
            (session_id,),
        ).fetchall()
    lines = [
        f"# Codex Council Transcript: {session_id}",
        "",
        f"- Objective: {session['objective']}",
        f"- Mode: {session['mode']}",
        f"- Allow writes: {bool(session['allow_writes'])}",
        f"- Status: {session['status']}",
        "",
        "## Messages",
    ]
    for row in messages:
        lines.extend(
            [
                f"### Message {row['id']}: {row['kind']} / {row['topic']}",
                "",
                f"- From: {row['from_agent']}",
                f"- To: {', '.join(from_json(row['to_agents_json'], []))}",
                f"- Requires response: {bool(row['requires_response'])}",
                f"- Artifact: {row['artifact_id'] or 'none'}",
                "",
                row["summary"],
                "",
            ]
        )
    lines.append("## Claims")
    for row in claims:
        lines.extend(
            [
                f"- `{row['id']}` ({row['confidence']}) by {row['from_agent']}: {row['statement']}",
                f"  Evidence: {', '.join(from_json(row['evidence_refs_json'], [])) or 'none'}",
            ]
        )
    lines.append("")
    lines.append("## Decisions")
    for row in decisions:
        lines.append(f"- `{row['id']}` {row['status']}: {row['title']} (proposed by {row['proposed_by']})")
        for vote in votes:
            if vote["decision_id"] == row["id"]:
                lines.append(f"  - {vote['agent_id']}: {vote['stance']} {vote['rationale']}".rstrip())
    validate_identifier(session_id, "session_id")
    path = safe_child_path(export_root(root), f"{session_id}-transcript.md", "transcript path")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"path": str(path)}


def tool_close_session(args: JsonDict) -> JsonDict:
    root = workspace_root(args)
    session_id = require_string(args, "session_id")
    status = optional_string(args, "status", "closed")
    registration_token = optional_string(args, "registration_token", "")
    closed_by = optional_string(args, "closed_by", "")
    agent_token = optional_string(args, "agent_token", "")
    if status not in {"closed", "cancelled", "archived"}:
        raise ValueError("status must be closed, cancelled, or archived")
    now = utc_now()
    with connect(root) as conn:
        session = ensure_session(conn, session_id)
        ensure_active_session(session)
        if token_matches(session["registration_token_hash"], registration_token):
            pass
        elif closed_by:
            ensure_chair_agent(conn, session_id, closed_by, agent_token)
        else:
            raise ValueError("close_session requires registration_token or chair agent_token")
        conn.execute(
            "UPDATE sessions SET status = ?, updated_at = ? WHERE id = ?",
            (status, now, session_id),
        )
    return {"session_id": session_id, "status": status, "updated_at": now, "closed_by": closed_by or "registration_token"}


def schema_object(properties: JsonDict, required: list[str]) -> JsonDict:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": properties,
        "required": required,
    }


S = {
    "string": {"type": "string"},
    "boolean": {"type": "boolean"},
    "integer": {"type": "integer"},
    "string_array": {"type": "array", "items": {"type": "string"}},
}


TOOLS: dict[str, tuple[str, JsonDict, Callable[[JsonDict], JsonDict]]] = {
    "create_session": (
        "Create a local Codex Council session in a workspace.",
        schema_object(
            {
                "workspace_root": S["string"],
                "objective": S["string"],
                "mode": {"type": "string", "enum": ["light", "standard", "deep", "review", "design", "implement"]},
                "allow_writes": S["boolean"],
                "session_id": S["string"],
            },
            ["workspace_root", "objective"],
        ),
        tool_create_session,
    ),
    "register_agent": (
        "Register or update an agent role in a session.",
        schema_object(
            {
                "workspace_root": S["string"],
                "session_id": S["string"],
                "agent_id": S["string"],
                "role": S["string"],
                "capabilities": S["string_array"],
                "registration_token": S["string"],
                "agent_token": S["string"],
            },
            ["workspace_root", "session_id", "agent_id", "role"],
        ),
        tool_register_agent,
    ),
    "rotate_registration_token": (
        "Rotate or recover a session registration token. Existing tokenized sessions require the current token.",
        schema_object(
            {
                "workspace_root": S["string"],
                "session_id": S["string"],
                "current_registration_token": S["string"],
            },
            ["workspace_root", "session_id"],
        ),
        tool_rotate_registration_token,
    ),
    "heartbeat_agent": (
        "Update an agent heartbeat timestamp.",
        schema_object(
            {
                "workspace_root": S["string"],
                "session_id": S["string"],
                "agent_id": S["string"],
                "agent_token": S["string"],
            },
            ["workspace_root", "session_id", "agent_id"],
        ),
        tool_heartbeat_agent,
    ),
    "post_message": (
        "Post a short message, optionally with long artifact content.",
        schema_object(
            {
                "workspace_root": S["string"],
                "session_id": S["string"],
                "from_agent": S["string"],
                "to_agents": S["string_array"],
                "topic": S["string"],
                "kind": S["string"],
                "summary": S["string"],
                "artifact_id": S["string"],
                "artifact_content": S["string"],
                "requires_response": S["boolean"],
                "agent_token": S["string"],
            },
            ["workspace_root", "session_id", "from_agent", "summary"],
        ),
        tool_post_message,
    ),
    "list_messages": (
        "List messages for an agent or a session.",
        schema_object(
            {
                "workspace_root": S["string"],
                "session_id": S["string"],
                "agent_id": S["string"],
                "topic": S["string"],
                "since_message_id": S["integer"],
                "include_acked": S["boolean"],
            },
            ["workspace_root", "session_id"],
        ),
        tool_list_messages,
    ),
    "ack_message": (
        "Mark a message as read by an agent.",
        schema_object(
            {
                "workspace_root": S["string"],
                "session_id": S["string"],
                "agent_id": S["string"],
                "message_id": S["integer"],
                "agent_token": S["string"],
            },
            ["workspace_root", "session_id", "agent_id", "message_id"],
        ),
        tool_ack_message,
    ),
    "put_artifact": (
        "Store long content as a session artifact.",
        schema_object(
            {
                "workspace_root": S["string"],
                "session_id": S["string"],
                "title": S["string"],
                "kind": S["string"],
                "created_by": S["string"],
                "content": S["string"],
                "agent_token": S["string"],
            },
            ["workspace_root", "session_id", "title", "created_by", "content"],
        ),
        tool_put_artifact,
    ),
    "get_artifact": (
        "Read a session artifact by id.",
        schema_object(
            {
                "workspace_root": S["string"],
                "session_id": S["string"],
                "artifact_id": S["string"],
                "agent_id": S["string"],
            },
            ["workspace_root", "session_id", "artifact_id"],
        ),
        tool_get_artifact,
    ),
    "create_task": (
        "Create a leaseable task for an agent.",
        schema_object(
            {
                "workspace_root": S["string"],
                "session_id": S["string"],
                "task_id": S["string"],
                "title": S["string"],
                "description": S["string"],
                "created_by": S["string"],
                "agent_token": S["string"],
                "artifact_id": S["string"],
            },
            ["workspace_root", "session_id", "title", "description", "created_by", "agent_token"],
        ),
        tool_create_task,
    ),
    "claim_task": (
        "Claim or renew a task lease.",
        schema_object(
            {
                "workspace_root": S["string"],
                "session_id": S["string"],
                "task_id": S["string"],
                "agent_id": S["string"],
                "agent_token": S["string"],
                "lease_seconds": S["integer"],
            },
            ["workspace_root", "session_id", "task_id", "agent_id", "agent_token"],
        ),
        tool_claim_task,
    ),
    "complete_task": (
        "Mark a task done.",
        schema_object(
            {
                "workspace_root": S["string"],
                "session_id": S["string"],
                "task_id": S["string"],
                "agent_id": S["string"],
                "agent_token": S["string"],
                "artifact_id": S["string"],
            },
            ["workspace_root", "session_id", "task_id", "agent_id", "agent_token", "artifact_id"],
        ),
        tool_complete_task,
    ),
    "append_claim": (
        "Append an evidence-backed claim.",
        schema_object(
            {
                "workspace_root": S["string"],
                "session_id": S["string"],
                "claim_id": S["string"],
                "from_agent": S["string"],
                "statement": S["string"],
                "confidence": S["string"],
                "evidence_refs": S["string_array"],
                "agent_token": S["string"],
            },
            ["workspace_root", "session_id", "from_agent", "statement"],
        ),
        tool_append_claim,
    ),
    "propose_decision": (
        "Create a proposed session decision.",
        schema_object(
            {
                "workspace_root": S["string"],
                "session_id": S["string"],
                "decision_id": S["string"],
                "title": S["string"],
                "proposed_by": S["string"],
                "rationale_artifact_id": S["string"],
                "agent_token": S["string"],
            },
            ["workspace_root", "session_id", "title", "proposed_by"],
        ),
        tool_propose_decision,
    ),
    "vote_decision": (
        "Vote on a proposed decision.",
        schema_object(
            {
                "workspace_root": S["string"],
                "session_id": S["string"],
                "decision_id": S["string"],
                "agent_id": S["string"],
                "stance": {"type": "string", "enum": ["approve", "reject", "abstain", "needs-work"]},
                "rationale": S["string"],
                "agent_token": S["string"],
            },
            ["workspace_root", "session_id", "decision_id", "agent_id", "stance"],
        ),
        tool_vote_decision,
    ),
    "get_session_state": (
        "Return counts, open tasks, and active agents.",
        schema_object({"workspace_root": S["string"], "session_id": S["string"]}, ["workspace_root", "session_id"]),
        tool_get_session_state,
    ),
    "export_transcript": (
        "Export a readable Markdown transcript.",
        schema_object({"workspace_root": S["string"], "session_id": S["string"]}, ["workspace_root", "session_id"]),
        tool_export_transcript,
    ),
    "close_session": (
        "Close, cancel, or archive a session.",
        schema_object(
            {
                "workspace_root": S["string"],
                "session_id": S["string"],
                "status": {"type": "string", "enum": ["closed", "cancelled", "archived"]},
                "registration_token": S["string"],
                "closed_by": S["string"],
                "agent_token": S["string"],
            },
            ["workspace_root", "session_id"],
        ),
        tool_close_session,
    ),
}


def list_tools() -> list[JsonDict]:
    return [
        {
            "name": name,
            "description": description,
            "inputSchema": schema,
        }
        for name, (description, schema, _handler) in TOOLS.items()
    ]


def call_tool(name: str, arguments: JsonDict) -> JsonDict:
    if name not in TOOLS:
        raise ValueError(f"unknown tool: {name}")
    handler = TOOLS[name][2]
    return handler(arguments)


def make_result(payload: JsonDict) -> JsonDict:
    return {
        "content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False, indent=2)}],
        "structuredContent": payload,
    }


def handle_request(message: JsonDict) -> JsonDict | None:
    method = message.get("method")
    msg_id = message.get("id")
    params = message.get("params") or {}
    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
                "instructions": INSTRUCTIONS,
            },
        }
    if method == "notifications/initialized":
        return None
    if method == "ping":
        return {"jsonrpc": "2.0", "id": msg_id, "result": {}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": msg_id, "result": {"tools": list_tools()}}
    if method == "tools/call":
        name = params.get("name")
        arguments = params.get("arguments") or {}
        if not isinstance(name, str):
            raise ValueError("tools/call params.name must be a string")
        if not isinstance(arguments, dict):
            raise ValueError("tools/call params.arguments must be an object")
        return {"jsonrpc": "2.0", "id": msg_id, "result": make_result(call_tool(name, arguments))}
    raise ValueError(f"method not found: {method}")


def error_response(msg_id: Any, exc: BaseException, debug: bool) -> JsonDict:
    data: JsonDict = {"type": exc.__class__.__name__}
    if debug:
        data["traceback"] = traceback.format_exc()
    return {
        "jsonrpc": "2.0",
        "id": msg_id,
        "error": {
            "code": -32000,
            "message": str(exc),
            "data": data,
        },
    }


def run_stdio(debug: bool = False) -> int:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        msg_id: Any = None
        try:
            message = json.loads(line)
            msg_id = message.get("id")
            response = handle_request(message)
        except Exception as exc:  # noqa: BLE001 - MCP must report structured tool errors.
            response = error_response(msg_id, exc, debug)
        if response is not None:
            sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
            sys.stdout.flush()
    return 0


def run_self_test() -> int:
    with contextlib.ExitStack() as stack:
        import tempfile

        temp_dir = pathlib.Path(stack.enter_context(tempfile.TemporaryDirectory()))
        session = tool_create_session({"workspace_root": str(temp_dir), "objective": "self test"})
        sid = session["session_id"]
        tool_register_agent(
            {
                "workspace_root": str(temp_dir),
                "session_id": sid,
                "agent_id": "architect",
                "role": "Architect",
            }
        )
        tool_register_agent(
            {
                "workspace_root": str(temp_dir),
                "session_id": sid,
                "agent_id": "skeptic",
                "role": "Skeptic",
            }
        )
        posted = tool_post_message(
            {
                "workspace_root": str(temp_dir),
                "session_id": sid,
                "from_agent": "architect",
                "to_agents": ["skeptic"],
                "summary": "Proposal ready",
                "artifact_content": "# Proposal\n",
            }
        )
        listed = tool_list_messages({"workspace_root": str(temp_dir), "session_id": sid, "agent_id": "skeptic"})
        assert listed["count"] == 1
        tool_ack_message({"workspace_root": str(temp_dir), "session_id": sid, "agent_id": "skeptic", "message_id": posted["message_id"]})
        listed_after_ack = tool_list_messages({"workspace_root": str(temp_dir), "session_id": sid, "agent_id": "skeptic"})
        assert listed_after_ack["count"] == 0
        transcript = tool_export_transcript({"workspace_root": str(temp_dir), "session_id": sid})
        assert pathlib.Path(transcript["path"]).exists()
    print("self-test ok")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Codex Council MCP server")
    parser.add_argument("--stdio", action="store_true", help="Run MCP over stdio")
    parser.add_argument("--self-test", action="store_true", help="Run a lightweight server self-test")
    parser.add_argument("--debug", action="store_true", help="Include tracebacks in MCP errors")
    args = parser.parse_args(argv)
    if args.self_test:
        return run_self_test()
    return run_stdio(debug=args.debug)


if __name__ == "__main__":
    raise SystemExit(main())
