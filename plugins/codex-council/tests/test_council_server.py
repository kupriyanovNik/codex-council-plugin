from __future__ import annotations

import importlib.util
import json
import pathlib
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
SERVER_PATH = ROOT / "mcp" / "council_server.py"

spec = importlib.util.spec_from_file_location("council_server", SERVER_PATH)
assert spec and spec.loader
council_server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(council_server)


def register_agent(tmp: str, session_id: str, agent_id: str, role: str | None = None) -> None:
    council_server.tool_register_agent(
        {
            "workspace_root": tmp,
            "session_id": session_id,
            "agent_id": agent_id,
            "role": role or agent_id.title(),
        }
    )


class CouncilServerTests(unittest.TestCase):
    def test_message_ack_and_artifact_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            session = council_server.tool_create_session(
                {
                    "workspace_root": tmp,
                    "objective": "test a blackboard session",
                    "mode": "standard",
                    "allow_writes": False,
                }
            )
            sid = session["session_id"]
            register_agent(tmp, sid, "architect", "Architect")
            register_agent(tmp, sid, "skeptic", "Skeptic")
            posted = council_server.tool_post_message(
                {
                    "workspace_root": tmp,
                    "session_id": sid,
                    "from_agent": "architect",
                    "to_agents": ["skeptic"],
                    "topic": "proposal",
                    "kind": "analysis",
                    "summary": "A bounded proposal is ready.",
                    "artifact_content": "# Proposal\n\nUse the MCP blackboard.\n",
                    "requires_response": True,
                }
            )
            inbox = council_server.tool_list_messages(
                {
                    "workspace_root": tmp,
                    "session_id": sid,
                    "agent_id": "skeptic",
                }
            )
            self.assertEqual(inbox["count"], 1)
            self.assertEqual(inbox["messages"][0]["artifact_id"], posted["artifact_id"])

            artifact = council_server.tool_get_artifact(
                {
                    "workspace_root": tmp,
                    "session_id": sid,
                    "artifact_id": posted["artifact_id"],
                }
            )
            self.assertIn("MCP blackboard", artifact["content"])

            council_server.tool_ack_message(
                {
                    "workspace_root": tmp,
                    "session_id": sid,
                    "agent_id": "skeptic",
                    "message_id": posted["message_id"],
                }
            )
            after_ack = council_server.tool_list_messages(
                {
                    "workspace_root": tmp,
                    "session_id": sid,
                    "agent_id": "skeptic",
                }
            )
            self.assertEqual(after_ack["count"], 0)

    def test_task_lease_blocks_competing_agent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            session = council_server.tool_create_session(
                {
                    "workspace_root": tmp,
                    "objective": "tasks",
                    "mode": "implement",
                    "allow_writes": True,
                }
            )
            sid = session["session_id"]
            register_agent(tmp, sid, "writer-a", "Writer")
            register_agent(tmp, sid, "writer-b", "Writer")
            task = council_server.tool_create_task(
                {
                    "workspace_root": tmp,
                    "session_id": sid,
                    "title": "Patch files",
                    "description": "Only one writer should own this task.",
                    "created_by": "writer-a",
                }
            )
            first = council_server.tool_claim_task(
                {
                    "workspace_root": tmp,
                    "session_id": sid,
                    "task_id": task["task_id"],
                    "agent_id": "writer-a",
                    "lease_seconds": 60,
                }
            )
            second = council_server.tool_claim_task(
                {
                    "workspace_root": tmp,
                    "session_id": sid,
                    "task_id": task["task_id"],
                    "agent_id": "writer-b",
                    "lease_seconds": 60,
                }
            )
            self.assertTrue(first["claimed"])
            self.assertFalse(second["claimed"])
            self.assertEqual(second["claimed_by"], "writer-a")
            renewed = council_server.tool_claim_task(
                {
                    "workspace_root": tmp,
                    "session_id": sid,
                    "task_id": task["task_id"],
                    "agent_id": "writer-a",
                    "lease_seconds": 120,
                }
            )
            self.assertTrue(renewed["claimed"])
            self.assertEqual(renewed["claimed_by"], "writer-a")

    def test_concurrent_task_claim_allows_only_one_winner(self) -> None:
        for _ in range(10):
            with tempfile.TemporaryDirectory() as tmp:
                session = council_server.tool_create_session(
                    {
                        "workspace_root": tmp,
                        "objective": "concurrent claim",
                        "mode": "implement",
                        "allow_writes": True,
                    }
                )
                sid = session["session_id"]
                register_agent(tmp, sid, "writer-a", "Writer")
                register_agent(tmp, sid, "writer-b", "Writer")
                task = council_server.tool_create_task(
                    {
                        "workspace_root": tmp,
                        "session_id": sid,
                        "title": "Patch files",
                        "description": "Only one concurrent writer should win.",
                        "created_by": "writer-a",
                    }
                )
                barrier = threading.Barrier(3)
                results = []
                errors = []

                def claim(agent_id: str) -> None:
                    try:
                        barrier.wait(timeout=5)
                        results.append(
                            council_server.tool_claim_task(
                                {
                                    "workspace_root": tmp,
                                    "session_id": sid,
                                    "task_id": task["task_id"],
                                    "agent_id": agent_id,
                                    "lease_seconds": 60,
                                }
                            )
                        )
                    except BaseException as exc:  # pragma: no cover - surfaced below.
                        errors.append(exc)

                threads = [threading.Thread(target=claim, args=(agent_id,)) for agent_id in ("writer-a", "writer-b")]
                for thread in threads:
                    thread.start()
                barrier.wait(timeout=5)
                for thread in threads:
                    thread.join(timeout=5)

                if errors:
                    raise errors[0]
                self.assertEqual(len(results), 2)
                winners = [result for result in results if result["claimed"]]
                self.assertEqual(len(winners), 1)
                with council_server.connect(pathlib.Path(tmp)) as conn:
                    row = conn.execute(
                        "SELECT claimed_by FROM tasks WHERE session_id = ? AND id = ?",
                        (sid, task["task_id"]),
                    ).fetchone()
                self.assertEqual(row["claimed_by"], winners[0]["claimed_by"])

    def test_write_tasks_require_write_session_and_claim(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            readonly = council_server.tool_create_session(
                {
                    "workspace_root": tmp,
                    "objective": "readonly",
                    "mode": "review",
                    "allow_writes": False,
                }
            )
            with self.assertRaisesRegex(ValueError, "does not allow"):
                council_server.tool_create_task(
                    {
                        "workspace_root": tmp,
                        "session_id": readonly["session_id"],
                        "title": "Patch files",
                        "description": "Must not be allowed in review mode.",
                        "created_by": "writer",
                    }
                )

            writable = council_server.tool_create_session(
                {
                    "workspace_root": tmp,
                    "objective": "write",
                    "mode": "implement",
                    "allow_writes": True,
                }
            )
            sid = writable["session_id"]
            register_agent(tmp, sid, "writer", "Writer")
            task = council_server.tool_create_task(
                {
                    "workspace_root": tmp,
                    "session_id": sid,
                    "title": "Patch files",
                    "description": "Completion requires an active lease.",
                    "created_by": "writer",
                }
            )
            artifact = council_server.tool_put_artifact(
                {
                    "workspace_root": tmp,
                    "session_id": sid,
                    "title": "Completion",
                    "kind": "implementation",
                    "created_by": "writer",
                    "content": "Changed files and verification.",
                }
            )
            with self.assertRaisesRegex(ValueError, "claimed before completion"):
                council_server.tool_complete_task(
                    {
                        "workspace_root": tmp,
                        "session_id": sid,
                        "task_id": task["task_id"],
                        "agent_id": "writer",
                        "artifact_id": artifact["artifact_id"],
                    }
                )

            council_server.tool_claim_task(
                {
                    "workspace_root": tmp,
                    "session_id": sid,
                    "task_id": task["task_id"],
                    "agent_id": "writer",
                    "lease_seconds": 60,
                    }
                )
            with self.assertRaisesRegex(ValueError, "requires artifact_id"):
                council_server.tool_complete_task(
                    {
                        "workspace_root": tmp,
                        "session_id": sid,
                        "task_id": task["task_id"],
                        "agent_id": "writer",
                    }
                )

            completed = council_server.tool_complete_task(
                {
                    "workspace_root": tmp,
                    "session_id": sid,
                    "task_id": task["task_id"],
                    "agent_id": "writer",
                    "artifact_id": artifact["artifact_id"],
                }
            )
            self.assertEqual(completed["status"], "done")
            after_done = council_server.tool_claim_task(
                {
                    "workspace_root": tmp,
                    "session_id": sid,
                    "task_id": task["task_id"],
                    "agent_id": "writer",
                    "lease_seconds": 60,
                }
            )
            self.assertFalse(after_done["claimed"])
            self.assertEqual(after_done["status"], "done")

    def test_create_task_requires_registered_creator(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            session = council_server.tool_create_session(
                {
                    "workspace_root": tmp,
                    "objective": "registered creator",
                    "mode": "implement",
                    "allow_writes": True,
                }
            )
            with self.assertRaisesRegex(ValueError, "unknown agent_id"):
                council_server.tool_create_task(
                    {
                        "workspace_root": tmp,
                        "session_id": session["session_id"],
                        "title": "Unowned task",
                        "description": "Must not be created by an unregistered actor.",
                        "created_by": "missing-writer",
                    }
                )

    def test_allow_writes_requires_implement_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, "allow_writes requires mode: implement"):
                council_server.tool_create_session(
                    {
                        "workspace_root": tmp,
                        "objective": "bad write mode",
                        "mode": "review",
                        "allow_writes": True,
                    }
                )

            session = council_server.tool_create_session(
                {
                    "workspace_root": tmp,
                    "objective": "legacy bad write mode",
                    "mode": "review",
                    "allow_writes": False,
                }
            )
            sid = session["session_id"]
            register_agent(tmp, sid, "writer", "Writer")
            with council_server.connect(pathlib.Path(tmp)) as conn:
                conn.execute("UPDATE sessions SET allow_writes = 1 WHERE id = ?", (sid,))

            with self.assertRaisesRegex(ValueError, "write-capable tasks require implement mode"):
                council_server.tool_create_task(
                    {
                        "workspace_root": tmp,
                        "session_id": sid,
                        "title": "Legacy bad write task",
                        "description": "Must not be allowed outside implement mode.",
                        "created_by": "writer",
                    }
                )

    def test_completion_artifact_must_be_created_by_completing_agent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            session = council_server.tool_create_session(
                {
                    "workspace_root": tmp,
                    "objective": "completion ownership",
                    "mode": "implement",
                    "allow_writes": True,
                }
            )
            sid = session["session_id"]
            register_agent(tmp, sid, "writer", "Writer")
            register_agent(tmp, sid, "architect", "Architect")
            task = council_server.tool_create_task(
                {
                    "workspace_root": tmp,
                    "session_id": sid,
                    "title": "Patch files",
                    "description": "Completion must use the writer's artifact.",
                    "created_by": "writer",
                }
            )
            council_server.tool_claim_task(
                {
                    "workspace_root": tmp,
                    "session_id": sid,
                    "task_id": task["task_id"],
                    "agent_id": "writer",
                    "lease_seconds": 60,
                }
            )
            artifact = council_server.tool_put_artifact(
                {
                    "workspace_root": tmp,
                    "session_id": sid,
                    "title": "Architect artifact",
                    "kind": "implementation",
                    "created_by": "architect",
                    "content": "Not the writer completion artifact.",
                }
            )
            with self.assertRaisesRegex(ValueError, "created by architect, not writer"):
                council_server.tool_complete_task(
                    {
                        "workspace_root": tmp,
                        "session_id": sid,
                        "task_id": task["task_id"],
                        "agent_id": "writer",
                        "artifact_id": artifact["artifact_id"],
                    }
                )

    def test_complete_task_rejects_expired_lease(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            session = council_server.tool_create_session(
                {
                    "workspace_root": tmp,
                    "objective": "expired completion",
                    "mode": "implement",
                    "allow_writes": True,
                }
            )
            sid = session["session_id"]
            register_agent(tmp, sid, "writer", "Writer")
            task = council_server.tool_create_task(
                {
                    "workspace_root": tmp,
                    "session_id": sid,
                    "title": "Patch files",
                    "description": "Expired leases must not complete.",
                    "created_by": "writer",
                }
            )
            artifact = council_server.tool_put_artifact(
                {
                    "workspace_root": tmp,
                    "session_id": sid,
                    "title": "Completion",
                    "kind": "implementation",
                    "created_by": "writer",
                    "content": "Changed files and verification.",
                }
            )
            council_server.tool_claim_task(
                {
                    "workspace_root": tmp,
                    "session_id": sid,
                    "task_id": task["task_id"],
                    "agent_id": "writer",
                    "lease_seconds": 60,
                }
            )
            with council_server.connect(pathlib.Path(tmp)) as conn:
                conn.execute("UPDATE tasks SET lease_expires_at = 0 WHERE session_id = ? AND id = ?", (sid, task["task_id"]))

            with self.assertRaisesRegex(ValueError, "task lease expired"):
                council_server.tool_complete_task(
                    {
                        "workspace_root": tmp,
                        "session_id": sid,
                        "task_id": task["task_id"],
                        "agent_id": "writer",
                        "artifact_id": artifact["artifact_id"],
                    }
                )

    def test_old_writer_cannot_complete_after_expired_lease_is_reclaimed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            session = council_server.tool_create_session(
                {
                    "workspace_root": tmp,
                    "objective": "reclaimed completion",
                    "mode": "implement",
                    "allow_writes": True,
                }
            )
            sid = session["session_id"]
            register_agent(tmp, sid, "writer-a", "Writer")
            register_agent(tmp, sid, "writer-b", "Writer")
            task = council_server.tool_create_task(
                {
                    "workspace_root": tmp,
                    "session_id": sid,
                    "title": "Patch files",
                    "description": "Old lease holders must not complete after reclaim.",
                    "created_by": "writer-a",
                }
            )
            artifact = council_server.tool_put_artifact(
                {
                    "workspace_root": tmp,
                    "session_id": sid,
                    "title": "Writer A completion",
                    "kind": "implementation",
                    "created_by": "writer-a",
                    "content": "Writer A stale completion.",
                }
            )
            council_server.tool_claim_task(
                {
                    "workspace_root": tmp,
                    "session_id": sid,
                    "task_id": task["task_id"],
                    "agent_id": "writer-a",
                    "lease_seconds": 60,
                }
            )
            with council_server.connect(pathlib.Path(tmp)) as conn:
                conn.execute("UPDATE tasks SET lease_expires_at = 0 WHERE session_id = ? AND id = ?", (sid, task["task_id"]))
            reclaimed = council_server.tool_claim_task(
                {
                    "workspace_root": tmp,
                    "session_id": sid,
                    "task_id": task["task_id"],
                    "agent_id": "writer-b",
                    "lease_seconds": 60,
                }
            )
            self.assertTrue(reclaimed["claimed"])

            with self.assertRaisesRegex(ValueError, "claimed by writer-b, not writer-a"):
                council_server.tool_complete_task(
                    {
                        "workspace_root": tmp,
                        "session_id": sid,
                        "task_id": task["task_id"],
                        "agent_id": "writer-a",
                        "artifact_id": artifact["artifact_id"],
                    }
                )
            with council_server.connect(pathlib.Path(tmp)) as conn:
                row = conn.execute(
                    "SELECT status, claimed_by, artifact_id FROM tasks WHERE session_id = ? AND id = ?",
                    (sid, task["task_id"]),
                ).fetchone()
            self.assertEqual(row["status"], "claimed")
            self.assertEqual(row["claimed_by"], "writer-b")
            self.assertIsNone(row["artifact_id"])

    def test_existing_task_tables_are_migrated_with_creator_column(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            council_dir = root / ".codex-council"
            council_dir.mkdir()
            with sqlite3.connect(council_dir / "council.sqlite") as conn:
                conn.execute(
                    """
                    CREATE TABLE tasks (
                      id TEXT PRIMARY KEY,
                      session_id TEXT NOT NULL,
                      title TEXT NOT NULL,
                      description TEXT NOT NULL,
                      status TEXT NOT NULL,
                      claimed_by TEXT,
                      lease_expires_at REAL,
                      artifact_id TEXT,
                      created_at TEXT NOT NULL,
                      updated_at TEXT NOT NULL
                    )
                    """
                )

            with council_server.connect(root) as conn:
                columns = {row["name"] for row in conn.execute("PRAGMA table_info(tasks)").fetchall()}
            self.assertIn("created_by", columns)

    def test_task_tool_schemas_require_runtime_required_fields(self) -> None:
        create_task_required = council_server.TOOLS["create_task"][1]["required"]
        complete_task_required = council_server.TOOLS["complete_task"][1]["required"]

        self.assertIn("created_by", create_task_required)
        self.assertIn("artifact_id", complete_task_required)

    def test_cross_session_ack_and_vote_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            first = council_server.tool_create_session({"workspace_root": tmp, "objective": "first"})
            second = council_server.tool_create_session({"workspace_root": tmp, "objective": "second"})
            register_agent(tmp, first["session_id"], "architect", "Architect")
            register_agent(tmp, second["session_id"], "skeptic", "Skeptic")
            message = council_server.tool_post_message(
                {
                    "workspace_root": tmp,
                    "session_id": first["session_id"],
                    "from_agent": "architect",
                    "summary": "Message in first session.",
                }
            )
            with self.assertRaisesRegex(ValueError, "unknown message_id"):
                council_server.tool_ack_message(
                    {
                        "workspace_root": tmp,
                        "session_id": second["session_id"],
                        "agent_id": "skeptic",
                        "message_id": message["message_id"],
                    }
                )

            decision = council_server.tool_propose_decision(
                {
                    "workspace_root": tmp,
                    "session_id": first["session_id"],
                    "title": "First session decision",
                    "proposed_by": "architect",
                }
            )
            with self.assertRaisesRegex(ValueError, "unknown decision_id"):
                council_server.tool_vote_decision(
                    {
                        "workspace_root": tmp,
                        "session_id": second["session_id"],
                        "decision_id": decision["decision_id"],
                        "agent_id": "skeptic",
                        "stance": "approve",
                    }
                )

    def test_artifact_references_must_belong_to_session(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            first = council_server.tool_create_session(
                {
                    "workspace_root": tmp,
                    "objective": "first",
                    "mode": "implement",
                    "allow_writes": True,
                }
            )
            second = council_server.tool_create_session(
                {
                    "workspace_root": tmp,
                    "objective": "second",
                    "mode": "implement",
                    "allow_writes": True,
                }
            )
            register_agent(tmp, first["session_id"], "architect", "Architect")
            first_artifact = council_server.tool_put_artifact(
                {
                    "workspace_root": tmp,
                    "session_id": first["session_id"],
                    "title": "First artifact",
                    "kind": "analysis",
                    "created_by": "architect",
                    "content": "Only belongs to the first session.",
                }
            )
            register_agent(tmp, second["session_id"], "architect", "Architect")
            register_agent(tmp, second["session_id"], "verifier", "Verifier")

            with self.assertRaisesRegex(ValueError, "unknown artifact_id"):
                council_server.tool_post_message(
                    {
                        "workspace_root": tmp,
                        "session_id": second["session_id"],
                        "from_agent": "architect",
                        "summary": "Bad artifact ref.",
                        "artifact_id": first_artifact["artifact_id"],
                    }
                )

            with self.assertRaisesRegex(ValueError, "unknown artifact_id"):
                council_server.tool_create_task(
                    {
                        "workspace_root": tmp,
                        "session_id": second["session_id"],
                        "title": "Bad artifact task",
                        "description": "Uses another session artifact.",
                        "created_by": "architect",
                        "artifact_id": first_artifact["artifact_id"],
                    }
                )

            with self.assertRaisesRegex(ValueError, "unknown artifact_id"):
                council_server.tool_propose_decision(
                    {
                        "workspace_root": tmp,
                        "session_id": second["session_id"],
                        "title": "Bad rationale",
                        "proposed_by": "architect",
                        "rationale_artifact_id": first_artifact["artifact_id"],
                    }
                )

            with self.assertRaisesRegex(ValueError, "unknown artifact_id"):
                council_server.tool_append_claim(
                    {
                        "workspace_root": tmp,
                        "session_id": second["session_id"],
                        "from_agent": "verifier",
                        "statement": "Bad evidence ref.",
                        "evidence_refs": [first_artifact["artifact_id"]],
                    }
                )

    def test_mutating_tools_require_registered_agents_and_active_session(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            session = council_server.tool_create_session({"workspace_root": tmp, "objective": "registration"})
            sid = session["session_id"]
            with self.assertRaisesRegex(ValueError, "unknown agent_id"):
                council_server.tool_post_message(
                    {
                        "workspace_root": tmp,
                        "session_id": sid,
                        "from_agent": "typo-agent",
                        "summary": "This agent never registered.",
                    }
                )

            register_agent(tmp, sid, "architect", "Architect")
            with self.assertRaisesRegex(ValueError, "unknown agent_id"):
                council_server.tool_post_message(
                    {
                        "workspace_root": tmp,
                        "session_id": sid,
                        "from_agent": "architect",
                        "to_agents": ["missing-agent"],
                        "summary": "The target never registered.",
                    }
                )

            council_server.tool_close_session({"workspace_root": tmp, "session_id": sid, "status": "closed"})
            state = council_server.tool_get_session_state({"workspace_root": tmp, "session_id": sid})
            self.assertEqual(state["session"]["status"], "closed")
            with self.assertRaisesRegex(ValueError, "session is not active"):
                council_server.tool_post_message(
                    {
                        "workspace_root": tmp,
                        "session_id": sid,
                        "from_agent": "architect",
                        "summary": "Closed sessions are immutable.",
                    }
                )

    def test_workspace_root_must_already_exist(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            missing = pathlib.Path(tmp) / "typo" / "nested"
            with self.assertRaisesRegex(ValueError, "workspace_root does not exist"):
                council_server.tool_create_session(
                    {
                        "workspace_root": str(missing),
                        "objective": "missing workspace",
                    }
                )
            self.assertFalse(missing.exists())

    def test_stdio_tools_list_smoke(self) -> None:
        proc = subprocess.run(
            [sys.executable, str(SERVER_PATH), "--stdio"],
            input='{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}\n',
            text=True,
            capture_output=True,
            check=True,
            timeout=5,
        )
        self.assertIn("create_session", proc.stdout)
        self.assertIn("post_message", proc.stdout)

    def test_stdio_create_session_tool_call(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            request = {
                "jsonrpc": "2.0",
                "id": 7,
                "method": "tools/call",
                "params": {
                    "name": "create_session",
                    "arguments": {
                        "workspace_root": tmp,
                        "objective": "stdio integration",
                        "mode": "standard",
                        "allow_writes": False,
                    },
                },
            }
            proc = subprocess.run(
                [sys.executable, str(SERVER_PATH), "--stdio"],
                input=json.dumps(request) + "\n",
                text=True,
                capture_output=True,
                check=True,
                timeout=5,
            )
            response = json.loads(proc.stdout)
            payload = response["result"]["structuredContent"]
            self.assertEqual(response["id"], 7)
            self.assertEqual(payload["workspace_root"], str(pathlib.Path(tmp).resolve()))
            self.assertTrue((pathlib.Path(tmp) / ".codex-council" / "council.sqlite").exists())


if __name__ == "__main__":
    unittest.main()
