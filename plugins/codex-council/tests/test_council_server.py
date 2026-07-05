from __future__ import annotations

import contextlib
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


def register_agent(
    tmp: str,
    session_id: str,
    agent_id: str,
    role: str | None = None,
    *,
    capabilities: list[str] | None = None,
    registration_token: str | None = None,
    agent_token: str | None = None,
) -> dict:
    args = {
        "workspace_root": tmp,
        "session_id": session_id,
        "agent_id": agent_id,
        "role": role or agent_id.title(),
    }
    if capabilities is not None:
        args["capabilities"] = capabilities
    if registration_token is not None:
        args["registration_token"] = registration_token
    if agent_token is not None:
        args["agent_token"] = agent_token
    return council_server.tool_register_agent(args)


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
            writer_a = register_agent(tmp, sid, "writer-a", "Writer", registration_token=session["registration_token"])
            writer_b = register_agent(tmp, sid, "writer-b", "Writer", registration_token=session["registration_token"])
            task = council_server.tool_create_task(
                {
                    "workspace_root": tmp,
                    "session_id": sid,
                    "title": "Patch files",
                    "description": "Only one writer should own this task.",
                    "created_by": "writer-a",
                    "agent_token": writer_a["agent_token"],
                }
            )
            first = council_server.tool_claim_task(
                {
                    "workspace_root": tmp,
                    "session_id": sid,
                    "task_id": task["task_id"],
                    "agent_id": "writer-a",
                    "agent_token": writer_a["agent_token"],
                    "lease_seconds": 60,
                }
            )
            second = council_server.tool_claim_task(
                {
                    "workspace_root": tmp,
                    "session_id": sid,
                    "task_id": task["task_id"],
                    "agent_id": "writer-b",
                    "agent_token": writer_b["agent_token"],
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
                    "agent_token": writer_a["agent_token"],
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
                writer_a = register_agent(tmp, sid, "writer-a", "Writer", registration_token=session["registration_token"])
                writer_b = register_agent(tmp, sid, "writer-b", "Writer", registration_token=session["registration_token"])
                writer_tokens = {"writer-a": writer_a["agent_token"], "writer-b": writer_b["agent_token"]}
                task = council_server.tool_create_task(
                    {
                        "workspace_root": tmp,
                        "session_id": sid,
                        "title": "Patch files",
                        "description": "Only one concurrent writer should win.",
                        "created_by": "writer-a",
                        "agent_token": writer_a["agent_token"],
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
                                    "agent_token": writer_tokens[agent_id],
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
                        "agent_token": "unused",
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
            writer = register_agent(tmp, sid, "writer", "Writer", registration_token=writable["registration_token"])
            task = council_server.tool_create_task(
                {
                    "workspace_root": tmp,
                    "session_id": sid,
                    "title": "Patch files",
                    "description": "Completion requires an active lease.",
                    "created_by": "writer",
                    "agent_token": writer["agent_token"],
                }
            )
            artifact = council_server.tool_put_artifact(
                {
                    "workspace_root": tmp,
                    "session_id": sid,
                    "title": "Completion",
                    "kind": "implementation",
                    "created_by": "writer",
                    "agent_token": writer["agent_token"],
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
                        "agent_token": writer["agent_token"],
                        "artifact_id": artifact["artifact_id"],
                    }
                )

            council_server.tool_claim_task(
                {
                    "workspace_root": tmp,
                    "session_id": sid,
                    "task_id": task["task_id"],
                    "agent_id": "writer",
                    "agent_token": writer["agent_token"],
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
                        "agent_token": writer["agent_token"],
                    }
                )

            completed = council_server.tool_complete_task(
                {
                    "workspace_root": tmp,
                    "session_id": sid,
                    "task_id": task["task_id"],
                    "agent_id": "writer",
                    "agent_token": writer["agent_token"],
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
                    "agent_token": writer["agent_token"],
                    "lease_seconds": 60,
                }
            )
            self.assertFalse(after_done["claimed"])
            self.assertEqual(after_done["status"], "done")

    def test_write_tasks_require_writer_or_chair_roles(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            session = council_server.tool_create_session(
                {
                    "workspace_root": tmp,
                    "objective": "role-gated writes",
                    "mode": "implement",
                    "allow_writes": True,
                }
            )
            sid = session["session_id"]
            chair = register_agent(tmp, sid, "chair", "Chair", registration_token=session["registration_token"])
            register_agent(tmp, sid, "reviewer", "Reviewer")
            writer = register_agent(tmp, sid, "writer", "Writer", registration_token=session["registration_token"])
            task = council_server.tool_create_task(
                {
                    "workspace_root": tmp,
                    "session_id": sid,
                    "title": "Patch files",
                    "description": "Only Writer should claim and complete.",
                    "created_by": "chair",
                    "agent_token": chair["agent_token"],
                }
            )
            with self.assertRaisesRegex(ValueError, "not allowed to claim or complete"):
                council_server.tool_claim_task(
                    {
                        "workspace_root": tmp,
                        "session_id": sid,
                        "task_id": task["task_id"],
                        "agent_id": "reviewer",
                        "agent_token": "not-a-writer-token",
                    }
                )
            claimed = council_server.tool_claim_task(
                {
                    "workspace_root": tmp,
                    "session_id": sid,
                    "task_id": task["task_id"],
                    "agent_id": "writer",
                    "agent_token": writer["agent_token"],
                }
            )
            self.assertTrue(claimed["claimed"])

            artifact = council_server.tool_put_artifact(
                {
                    "workspace_root": tmp,
                    "session_id": sid,
                    "title": "Completion",
                    "created_by": "writer",
                    "agent_token": writer["agent_token"],
                    "content": "Changed files and verification.",
                }
            )
            completed = council_server.tool_complete_task(
                {
                    "workspace_root": tmp,
                    "session_id": sid,
                    "task_id": task["task_id"],
                    "agent_id": "writer",
                    "agent_token": writer["agent_token"],
                    "artifact_id": artifact["artifact_id"],
                }
            )
            self.assertEqual(completed["status"], "done")

    def test_non_task_manager_cannot_create_write_tasks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            session = council_server.tool_create_session(
                {
                    "workspace_root": tmp,
                    "objective": "task creator gate",
                    "mode": "implement",
                    "allow_writes": True,
                }
            )
            sid = session["session_id"]
            register_agent(tmp, sid, "reviewer", "Reviewer")
            with self.assertRaisesRegex(ValueError, "not allowed to create write tasks"):
                council_server.tool_create_task(
                    {
                        "workspace_root": tmp,
                        "session_id": sid,
                        "title": "Bad task",
                        "description": "Reviewer should not create write tasks.",
                        "created_by": "reviewer",
                        "agent_token": "not-a-task-creator-token",
                    }
                )

    def test_write_capability_can_claim_tasks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            session = council_server.tool_create_session(
                {
                    "workspace_root": tmp,
                    "objective": "capability-gated writes",
                    "mode": "implement",
                    "allow_writes": True,
                }
            )
            sid = session["session_id"]
            writer = register_agent(
                tmp,
                sid,
                "agent-with-write-capability",
                "Assistant",
                capabilities=["write"],
                registration_token=session["registration_token"],
            )
            task = council_server.tool_create_task(
                {
                    "workspace_root": tmp,
                    "session_id": sid,
                    "title": "Patch files",
                    "description": "Capability should be sufficient.",
                    "created_by": "agent-with-write-capability",
                    "agent_token": writer["agent_token"],
                }
            )
            claimed = council_server.tool_claim_task(
                {
                    "workspace_root": tmp,
                    "session_id": sid,
                    "task_id": task["task_id"],
                    "agent_id": "agent-with-write-capability",
                    "agent_token": writer["agent_token"],
                }
            )
            self.assertTrue(claimed["claimed"])

    def test_privileged_registration_requires_session_token_and_agent_token(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            session = council_server.tool_create_session(
                {
                    "workspace_root": tmp,
                    "objective": "registration token gate",
                    "mode": "implement",
                    "allow_writes": True,
                }
            )
            sid = session["session_id"]
            reviewer = register_agent(tmp, sid, "reviewer", "Reviewer", capabilities=["read", "review"])
            self.assertNotIn("agent_token", reviewer)

            with self.assertRaisesRegex(ValueError, "role/capabilities are immutable"):
                register_agent(tmp, sid, "reviewer", "Reviewer", capabilities=["write"])
            with self.assertRaisesRegex(ValueError, "role/capabilities are immutable"):
                register_agent(
                    tmp,
                    sid,
                    "reviewer",
                    "Writer",
                    registration_token=session["registration_token"],
                )
            with self.assertRaisesRegex(ValueError, "valid registration_token"):
                register_agent(tmp, sid, "late-writer", "Writer")

            writer = register_agent(tmp, sid, "writer", "Writer", registration_token=session["registration_token"])
            chair = register_agent(tmp, sid, "chair", "Chair", registration_token=session["registration_token"])
            task = council_server.tool_create_task(
                {
                    "workspace_root": tmp,
                    "session_id": sid,
                    "title": "Patch files",
                    "description": "Requires a valid chair token.",
                    "created_by": "chair",
                    "agent_token": chair["agent_token"],
                }
            )
            with self.assertRaisesRegex(ValueError, "valid agent_token"):
                council_server.tool_claim_task(
                    {
                        "workspace_root": tmp,
                        "session_id": sid,
                        "task_id": task["task_id"],
                        "agent_id": "writer",
                        "agent_token": "wrong-token",
                    }
                )
            claimed = council_server.tool_claim_task(
                {
                    "workspace_root": tmp,
                    "session_id": sid,
                    "task_id": task["task_id"],
                    "agent_id": "writer",
                    "agent_token": writer["agent_token"],
                }
            )
            self.assertTrue(claimed["claimed"])
            state = council_server.tool_get_session_state({"workspace_root": tmp, "session_id": sid})
            reviewer_state = next(agent for agent in state["agents"] if agent["agent_id"] == "reviewer")
            self.assertEqual(reviewer_state["role"], "Reviewer")
            self.assertEqual(reviewer_state["capabilities"], ["read", "review"])

    def test_tokenized_agent_reregistration_requires_agent_token(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            session = council_server.tool_create_session(
                {
                    "workspace_root": tmp,
                    "objective": "privileged reregister",
                    "mode": "implement",
                    "allow_writes": True,
                }
            )
            sid = session["session_id"]
            writer = register_agent(tmp, sid, "writer", "Writer", registration_token=session["registration_token"])

            with self.assertRaisesRegex(ValueError, "valid agent_token"):
                register_agent(tmp, sid, "writer", "Writer")

            refreshed = register_agent(tmp, sid, "writer", "Writer", agent_token=writer["agent_token"])
            self.assertTrue(refreshed["registered"])
            self.assertNotIn("agent_token", refreshed)

    def test_close_session_requires_registration_or_chair_token(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            session = council_server.tool_create_session(
                {
                    "workspace_root": tmp,
                    "objective": "close auth",
                    "mode": "implement",
                    "allow_writes": True,
                }
            )
            sid = session["session_id"]
            chair = register_agent(tmp, sid, "chair", "Chair", registration_token=session["registration_token"])
            writer = register_agent(tmp, sid, "writer", "Writer", registration_token=session["registration_token"])
            register_agent(tmp, sid, "reviewer", "Reviewer")

            with self.assertRaisesRegex(ValueError, "requires registration_token or chair agent_token"):
                council_server.tool_close_session({"workspace_root": tmp, "session_id": sid})
            with self.assertRaisesRegex(ValueError, "not allowed to close sessions"):
                council_server.tool_close_session(
                    {
                        "workspace_root": tmp,
                        "session_id": sid,
                        "closed_by": "writer",
                        "agent_token": writer["agent_token"],
                    }
                )
            with self.assertRaisesRegex(ValueError, "valid agent_token"):
                council_server.tool_close_session(
                    {
                        "workspace_root": tmp,
                        "session_id": sid,
                        "closed_by": "chair",
                        "agent_token": "wrong-token",
                    }
                )

            closed = council_server.tool_close_session(
                {
                    "workspace_root": tmp,
                    "session_id": sid,
                    "closed_by": "chair",
                    "agent_token": chair["agent_token"],
                }
            )
            self.assertEqual(closed["status"], "closed")

        with tempfile.TemporaryDirectory() as tmp:
            session = council_server.tool_create_session({"workspace_root": tmp, "objective": "close with session token"})
            closed = council_server.tool_close_session(
                {
                    "workspace_root": tmp,
                    "session_id": session["session_id"],
                    "status": "archived",
                    "registration_token": session["registration_token"],
                }
            )
            self.assertEqual(closed["status"], "archived")

    def test_privileged_identity_requires_agent_token_for_mutations(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            session = council_server.tool_create_session(
                {
                    "workspace_root": tmp,
                    "objective": "privileged identity integrity",
                    "mode": "implement",
                    "allow_writes": True,
                }
            )
            sid = session["session_id"]
            writer = register_agent(tmp, sid, "writer", "Writer", registration_token=session["registration_token"])
            register_agent(tmp, sid, "reviewer", "Reviewer")

            protected_calls = [
                (
                    council_server.tool_heartbeat_agent,
                    {"workspace_root": tmp, "session_id": sid, "agent_id": "writer"},
                ),
                (
                    council_server.tool_put_artifact,
                    {
                        "workspace_root": tmp,
                        "session_id": sid,
                        "title": "Spoofed artifact",
                        "created_by": "writer",
                        "content": "No writer token.",
                    },
                ),
                (
                    council_server.tool_post_message,
                    {
                        "workspace_root": tmp,
                        "session_id": sid,
                        "from_agent": "writer",
                        "summary": "No writer token.",
                    },
                ),
                (
                    council_server.tool_append_claim,
                    {
                        "workspace_root": tmp,
                        "session_id": sid,
                        "from_agent": "writer",
                        "statement": "No writer token.",
                    },
                ),
                (
                    council_server.tool_propose_decision,
                    {
                        "workspace_root": tmp,
                        "session_id": sid,
                        "title": "No writer token",
                        "proposed_by": "writer",
                    },
                ),
            ]
            for tool, args in protected_calls:
                with self.subTest(tool=tool.__name__):
                    with self.assertRaisesRegex(ValueError, "valid agent_token"):
                        tool(args)

            artifact = council_server.tool_put_artifact(
                {
                    "workspace_root": tmp,
                    "session_id": sid,
                    "title": "Writer artifact",
                    "created_by": "writer",
                    "agent_token": writer["agent_token"],
                    "content": "Authenticated writer content.",
                }
            )
            posted = council_server.tool_post_message(
                {
                    "workspace_root": tmp,
                    "session_id": sid,
                    "from_agent": "writer",
                    "summary": "Authenticated writer message.",
                    "agent_token": writer["agent_token"],
                }
            )
            council_server.tool_ack_message(
                {
                    "workspace_root": tmp,
                    "session_id": sid,
                    "agent_id": "writer",
                    "message_id": posted["message_id"],
                    "agent_token": writer["agent_token"],
                }
            )
            decision = council_server.tool_propose_decision(
                {
                    "workspace_root": tmp,
                    "session_id": sid,
                    "title": "Authenticated writer decision",
                    "proposed_by": "writer",
                    "rationale_artifact_id": artifact["artifact_id"],
                    "agent_token": writer["agent_token"],
                }
            )
            with self.assertRaisesRegex(ValueError, "valid agent_token"):
                council_server.tool_vote_decision(
                    {
                        "workspace_root": tmp,
                        "session_id": sid,
                        "decision_id": decision["decision_id"],
                        "agent_id": "writer",
                        "stance": "approve",
                    }
                )
            voted = council_server.tool_vote_decision(
                {
                    "workspace_root": tmp,
                    "session_id": sid,
                    "decision_id": decision["decision_id"],
                    "agent_id": "writer",
                    "stance": "approve",
                    "agent_token": writer["agent_token"],
                }
            )
            self.assertEqual(voted["stance"], "approve")

            reviewer_message = council_server.tool_post_message(
                {
                    "workspace_root": tmp,
                    "session_id": sid,
                    "from_agent": "reviewer",
                    "summary": "Reviewer remains tokenless.",
                }
            )
            self.assertGreater(reviewer_message["message_id"], 0)

    def test_concurrent_registration_cannot_mix_role_and_token(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            session = council_server.tool_create_session(
                {
                    "workspace_root": tmp,
                    "objective": "registration race",
                    "mode": "implement",
                    "allow_writes": True,
                }
            )
            sid = session["session_id"]
            results: list[dict] = []
            errors: list[str] = []

            def register(role: str) -> None:
                try:
                    args = {
                        "workspace_root": tmp,
                        "session_id": sid,
                        "agent_id": "same-agent",
                        "role": role,
                    }
                    if role == "Writer":
                        args["registration_token"] = session["registration_token"]
                    results.append(council_server.tool_register_agent(args))
                except Exception as exc:
                    errors.append(str(exc))

            threads = [threading.Thread(target=register, args=(role,)) for role in ("Writer", "Reviewer")]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()

            self.assertEqual(len(results), 1)
            self.assertEqual(len(errors), 1)
            self.assertIn("role/capabilities are immutable", errors[0])
            with council_server.connect(pathlib.Path(tmp)) as conn:
                row = conn.execute(
                    "SELECT role, agent_token_hash FROM agents WHERE session_id = ? AND agent_id = ?",
                    (sid, "same-agent"),
                ).fetchone()
            self.assertIsNotNone(row)
            if row["role"] == "Writer":
                self.assertIsNotNone(row["agent_token_hash"])
                self.assertIn("agent_token", results[0])
            else:
                self.assertEqual(row["role"], "Reviewer")
                self.assertIsNone(row["agent_token_hash"])
                self.assertNotIn("agent_token", results[0])

    def test_manage_tasks_capability_does_not_grant_write_task_creation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            session = council_server.tool_create_session(
                {
                    "workspace_root": tmp,
                    "objective": "manage_tasks should not widen auth",
                    "mode": "implement",
                    "allow_writes": True,
                }
            )
            sid = session["session_id"]
            manager = register_agent(tmp, sid, "manager", "Assistant", capabilities=["manage_tasks"])
            self.assertNotIn("agent_token", manager)
            with self.assertRaisesRegex(ValueError, "not allowed to create write tasks"):
                council_server.tool_create_task(
                    {
                        "workspace_root": tmp,
                        "session_id": sid,
                        "title": "Bad task",
                        "description": "manage_tasks is not a write capability.",
                        "created_by": "manager",
                        "agent_token": "missing-token",
                    }
                )

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
                        "agent_token": "missing-token",
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
            writer = register_agent(tmp, sid, "writer", "Writer", registration_token=session["registration_token"])
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
                        "agent_token": writer["agent_token"],
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
            writer = register_agent(tmp, sid, "writer", "Writer", registration_token=session["registration_token"])
            register_agent(tmp, sid, "architect", "Architect")
            task = council_server.tool_create_task(
                {
                    "workspace_root": tmp,
                    "session_id": sid,
                    "title": "Patch files",
                    "description": "Completion must use the writer's artifact.",
                    "created_by": "writer",
                    "agent_token": writer["agent_token"],
                }
            )
            council_server.tool_claim_task(
                {
                    "workspace_root": tmp,
                    "session_id": sid,
                    "task_id": task["task_id"],
                    "agent_id": "writer",
                    "agent_token": writer["agent_token"],
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
                        "agent_token": writer["agent_token"],
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
            writer = register_agent(tmp, sid, "writer", "Writer", registration_token=session["registration_token"])
            task = council_server.tool_create_task(
                {
                    "workspace_root": tmp,
                    "session_id": sid,
                    "title": "Patch files",
                    "description": "Expired leases must not complete.",
                    "created_by": "writer",
                    "agent_token": writer["agent_token"],
                }
            )
            artifact = council_server.tool_put_artifact(
                {
                    "workspace_root": tmp,
                    "session_id": sid,
                    "title": "Completion",
                    "kind": "implementation",
                    "created_by": "writer",
                    "agent_token": writer["agent_token"],
                    "content": "Changed files and verification.",
                }
            )
            council_server.tool_claim_task(
                {
                    "workspace_root": tmp,
                    "session_id": sid,
                    "task_id": task["task_id"],
                    "agent_id": "writer",
                    "agent_token": writer["agent_token"],
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
                        "agent_token": writer["agent_token"],
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
            writer_a = register_agent(tmp, sid, "writer-a", "Writer", registration_token=session["registration_token"])
            writer_b = register_agent(tmp, sid, "writer-b", "Writer", registration_token=session["registration_token"])
            task = council_server.tool_create_task(
                {
                    "workspace_root": tmp,
                    "session_id": sid,
                    "title": "Patch files",
                    "description": "Old lease holders must not complete after reclaim.",
                    "created_by": "writer-a",
                    "agent_token": writer_a["agent_token"],
                }
            )
            artifact = council_server.tool_put_artifact(
                {
                    "workspace_root": tmp,
                    "session_id": sid,
                    "title": "Writer A completion",
                    "kind": "implementation",
                    "created_by": "writer-a",
                    "agent_token": writer_a["agent_token"],
                    "content": "Writer A stale completion.",
                }
            )
            council_server.tool_claim_task(
                {
                    "workspace_root": tmp,
                    "session_id": sid,
                    "task_id": task["task_id"],
                    "agent_id": "writer-a",
                    "agent_token": writer_a["agent_token"],
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
                    "agent_token": writer_b["agent_token"],
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
                        "agent_token": writer_a["agent_token"],
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
            with contextlib.closing(sqlite3.connect(council_dir / "council.sqlite")) as conn:
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
                conn.commit()

            with council_server.connect(root) as conn:
                task_columns = {row["name"] for row in conn.execute("PRAGMA table_info(tasks)").fetchall()}
                session_columns = {row["name"] for row in conn.execute("PRAGMA table_info(sessions)").fetchall()}
                agent_columns = {row["name"] for row in conn.execute("PRAGMA table_info(agents)").fetchall()}
            self.assertIn("created_by", task_columns)
            self.assertIn("registration_token_hash", session_columns)
            self.assertIn("agent_token_hash", agent_columns)

    def test_connect_closes_sqlite_connection_after_context(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with council_server.connect(pathlib.Path(tmp)) as conn:
                conn.execute("SELECT 1")
            with self.assertRaises(sqlite3.ProgrammingError):
                conn.execute("SELECT 1")

    def test_legacy_session_can_recover_registration_token(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            council_dir = root / ".codex-council"
            council_dir.mkdir()
            with contextlib.closing(sqlite3.connect(council_dir / "council.sqlite")) as conn:
                conn.execute(
                    """
                    CREATE TABLE sessions (
                      id TEXT PRIMARY KEY,
                      workspace_root TEXT NOT NULL,
                      objective TEXT NOT NULL,
                      mode TEXT NOT NULL,
                      allow_writes INTEGER NOT NULL DEFAULT 0,
                      status TEXT NOT NULL DEFAULT 'active',
                      created_at TEXT NOT NULL,
                      updated_at TEXT NOT NULL
                    )
                    """
                )
                conn.execute(
                    """
                    CREATE TABLE agents (
                      session_id TEXT NOT NULL,
                      agent_id TEXT NOT NULL,
                      role TEXT NOT NULL,
                      capabilities_json TEXT NOT NULL DEFAULT '[]',
                      registered_at TEXT NOT NULL,
                      heartbeat_at TEXT NOT NULL,
                      PRIMARY KEY (session_id, agent_id)
                    )
                    """
                )
                conn.execute(
                    "INSERT INTO sessions VALUES ('legacy-session', ?, 'legacy', 'implement', 1, 'active', 'now', 'now')",
                    (str(root),),
                )
                conn.execute(
                    "INSERT INTO agents VALUES ('legacy-session', 'writer', 'Writer', '[]', 'now', 'now')"
                )
                conn.commit()

            recovered = council_server.tool_rotate_registration_token(
                {
                    "workspace_root": tmp,
                    "session_id": "legacy-session",
                }
            )
            self.assertTrue(recovered["legacy_recovery"])
            writer = council_server.tool_register_agent(
                {
                    "workspace_root": tmp,
                    "session_id": "legacy-session",
                    "agent_id": "writer",
                    "role": "Writer",
                    "registration_token": recovered["registration_token"],
                }
            )
            task = council_server.tool_create_task(
                {
                    "workspace_root": tmp,
                    "session_id": "legacy-session",
                    "title": "Recovered task",
                    "description": "Legacy writer should work after recovery.",
                    "created_by": "writer",
                    "agent_token": writer["agent_token"],
                }
            )
            claimed = council_server.tool_claim_task(
                {
                    "workspace_root": tmp,
                    "session_id": "legacy-session",
                    "task_id": task["task_id"],
                    "agent_id": "writer",
                    "agent_token": writer["agent_token"],
                }
            )
            self.assertTrue(claimed["claimed"])

    def test_registration_token_rotation_requires_current_token_and_scrubs_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            session = council_server.tool_create_session({"workspace_root": tmp, "objective": "rotation"})
            sid = session["session_id"]
            state = council_server.tool_get_session_state({"workspace_root": tmp, "session_id": sid})
            self.assertNotIn("registration_token_hash", state["session"])
            self.assertTrue(state["session"]["has_registration_token"])

            with self.assertRaisesRegex(ValueError, "current_registration_token"):
                council_server.tool_rotate_registration_token(
                    {
                        "workspace_root": tmp,
                        "session_id": sid,
                        "current_registration_token": "wrong-token",
                    }
                )

            rotated = council_server.tool_rotate_registration_token(
                {
                    "workspace_root": tmp,
                    "session_id": sid,
                    "current_registration_token": session["registration_token"],
                }
            )
            self.assertFalse(rotated["legacy_recovery"])
            with self.assertRaisesRegex(ValueError, "valid registration_token"):
                register_agent(tmp, sid, "writer", "Writer", registration_token=session["registration_token"])
            writer = register_agent(tmp, sid, "writer", "Writer", registration_token=rotated["registration_token"])
            self.assertIn("agent_token", writer)

    def test_task_tool_schemas_require_runtime_required_fields(self) -> None:
        create_task_required = council_server.TOOLS["create_task"][1]["required"]
        claim_task_required = council_server.TOOLS["claim_task"][1]["required"]
        complete_task_required = council_server.TOOLS["complete_task"][1]["required"]
        register_agent_schema = council_server.TOOLS["register_agent"][1]
        close_session_schema = council_server.TOOLS["close_session"][1]
        tokenized_identity_tools = [
            "heartbeat_agent",
            "post_message",
            "ack_message",
            "put_artifact",
            "append_claim",
            "propose_decision",
            "vote_decision",
        ]

        self.assertIn("created_by", create_task_required)
        self.assertIn("agent_token", create_task_required)
        self.assertIn("agent_token", claim_task_required)
        self.assertIn("artifact_id", complete_task_required)
        self.assertIn("agent_token", complete_task_required)
        self.assertIn("agent_token", register_agent_schema["properties"])
        self.assertIn("registration_token", close_session_schema["properties"])
        self.assertIn("closed_by", close_session_schema["properties"])
        self.assertIn("agent_token", close_session_schema["properties"])
        self.assertNotIn("registration_token", close_session_schema["required"])
        self.assertNotIn("closed_by", close_session_schema["required"])
        self.assertNotIn("agent_token", close_session_schema["required"])
        for tool_name in tokenized_identity_tools:
            with self.subTest(tool_name=tool_name):
                schema = council_server.TOOLS[tool_name][1]
                self.assertIn("agent_token", schema["properties"])
                self.assertNotIn("agent_token", schema["required"])

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
            chair = register_agent(
                tmp,
                second["session_id"],
                "chair",
                "Chair",
                registration_token=second["registration_token"],
            )
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
                        "created_by": "chair",
                        "agent_token": chair["agent_token"],
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

            council_server.tool_close_session(
                {
                    "workspace_root": tmp,
                    "session_id": sid,
                    "status": "closed",
                    "registration_token": session["registration_token"],
                }
            )
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

    def test_session_id_must_not_escape_council_directories(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = pathlib.Path(tmp) / "workspace"
            workspace.mkdir()
            bad_ids = [
                "../outside",
                "../../outside",
                str(pathlib.Path(tmp) / "absolute-session"),
                ".hidden",
                "session/with/slash",
                "session\\with\\backslash",
            ]
            for bad_id in bad_ids:
                with self.subTest(bad_id=bad_id):
                    with self.assertRaisesRegex(ValueError, "session_id must be a simple identifier"):
                        council_server.tool_create_session(
                            {
                                "workspace_root": str(workspace),
                                "objective": "bad session id",
                                "session_id": bad_id,
                            }
                )
            self.assertFalse((pathlib.Path(tmp) / "outside-transcript.md").exists())

    def test_council_storage_directories_must_not_be_symlinks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = pathlib.Path(tmp) / "workspace"
            outside = pathlib.Path(tmp) / "outside"
            workspace.mkdir()
            outside.mkdir()

            (workspace / ".codex-council").symlink_to(outside, target_is_directory=True)
            with self.assertRaisesRegex(ValueError, "council directory must not be a symlink"):
                council_server.tool_create_session(
                    {
                        "workspace_root": str(workspace),
                        "objective": "symlinked council root",
                    }
                )

        for sqlite_name in council_server.SQLITE_STATE_FILES:
            with self.subTest(sqlite_name=sqlite_name):
                with tempfile.TemporaryDirectory() as tmp:
                    workspace = pathlib.Path(tmp) / "workspace"
                    outside = pathlib.Path(tmp) / "outside"
                    workspace.mkdir()
                    outside.mkdir()
                    council_dir = workspace / ".codex-council"
                    council_dir.mkdir()
                    outside_file = outside / sqlite_name
                    (council_dir / sqlite_name).symlink_to(outside_file)
                    with self.assertRaisesRegex(ValueError, "SQLite state file must not be a symlink"):
                        council_server.tool_create_session(
                            {
                                "workspace_root": str(workspace),
                                "objective": "symlinked sqlite state",
                            }
                        )
                    self.assertFalse(outside_file.exists())

        with tempfile.TemporaryDirectory() as tmp:
            workspace = pathlib.Path(tmp) / "workspace"
            outside = pathlib.Path(tmp) / "outside"
            workspace.mkdir()
            outside.mkdir()
            council_dir = workspace / ".codex-council"
            council_dir.mkdir()
            (council_dir / "artifacts").symlink_to(outside, target_is_directory=True)
            with self.assertRaisesRegex(ValueError, "artifact root must not be a symlink"):
                council_server.tool_create_session(
                    {
                        "workspace_root": str(workspace),
                        "objective": "symlinked artifact root",
                        "session_id": "failed-session",
                    }
                )
            with contextlib.closing(sqlite3.connect(council_dir / "council.sqlite")) as conn:
                count = conn.execute(
                    "SELECT COUNT(*) FROM sessions WHERE id = 'failed-session'"
                ).fetchone()[0]
            self.assertEqual(count, 0)

        with tempfile.TemporaryDirectory() as tmp:
            workspace = pathlib.Path(tmp) / "workspace"
            outside = pathlib.Path(tmp) / "outside"
            workspace.mkdir()
            outside.mkdir()
            session = council_server.tool_create_session(
                {
                    "workspace_root": str(workspace),
                    "objective": "symlinked export root",
                }
            )
            export_dir = workspace / ".codex-council" / "exports"
            export_dir.symlink_to(outside, target_is_directory=True)
            with self.assertRaisesRegex(ValueError, "export root must not be a symlink"):
                council_server.tool_export_transcript(
                    {
                        "workspace_root": str(workspace),
                        "session_id": session["session_id"],
                    }
                )

    def test_artifact_read_can_require_registered_reader_when_supplied(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            session = council_server.tool_create_session({"workspace_root": tmp, "objective": "reader registration"})
            sid = session["session_id"]
            register_agent(tmp, sid, "architect", "Architect")
            register_agent(tmp, sid, "skeptic", "Skeptic")
            posted = council_server.tool_post_message(
                {
                    "workspace_root": tmp,
                    "session_id": sid,
                    "from_agent": "architect",
                    "to_agents": ["skeptic"],
                    "summary": "Artifact ready",
                    "artifact_content": "Shared blackboard artifact.",
                }
            )
            with self.assertRaisesRegex(ValueError, "unknown agent_id"):
                council_server.tool_get_artifact(
                    {
                        "workspace_root": tmp,
                        "session_id": sid,
                        "artifact_id": posted["artifact_id"],
                        "agent_id": "missing-reader",
                    }
                )
            artifact = council_server.tool_get_artifact(
                {
                    "workspace_root": tmp,
                    "session_id": sid,
                    "artifact_id": posted["artifact_id"],
                    "agent_id": "skeptic",
                }
            )
            self.assertEqual(artifact["content"], "Shared blackboard artifact.")

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
