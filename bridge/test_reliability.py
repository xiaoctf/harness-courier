"""Audit regressions: isolated mailboxes only; no desktop input or worker launch."""

import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import dispatch_queue as queue
from receiver_hook import process

from bridge import Mailbox, invoke


class ReliabilityTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.box = Mailbox(self.root / "audit.db")
        self.box.register("zcode", "audit-A", "Audit A", "E:/audit/A", True)
        self.box.register("zcode", "audit-B", "Audit B", "E:/audit/B", True)
        self.box.bind("audit", "zcode", "audit-A")
        root_patch = patch.object(queue, "ROOT", self.root)
        root_patch.start()
        self.addCleanup(root_patch.stop)

    def message(self):
        return self.box.send("audit", "Isolated audit fixture")["id"]

    def job(self, **options):
        mid = self.message()
        queue.enqueue(self.box, mid, start=False, **options)
        return mid

    def failure(self, mid, **options):
        return {
            **self.box.status(mid),
            "dispatch_error": "Connection timed out",
            "desktop_delivery": {"retry_safe": True, "reason": "Connection timed out"},
        }

    def ack(self, mid):
        self.box.receive("zcode", "audit-A", mid)
        self.box.acknowledge("zcode", "audit-A", mid)

    def test_string_false_cannot_replace_binding(self):
        with self.assertRaises(ValueError):
            invoke(
                self.box,
                "codex",
                "bridge_bind",
                {
                    "alias": "audit",
                    "harness": "zcode",
                    "session_id": "audit-B",
                    "replace": "false",
                },
            )
        self.assertEqual(self.box.binding("audit")["session_id"], "audit-A")

    def test_non_string_title_cannot_be_persisted(self):
        with self.assertRaises(ValueError):
            invoke(
                self.box,
                "codex",
                "bridge_bind",
                {
                    "alias": "new-alias",
                    "harness": "zcode",
                    "session_id": "audit-A",
                    "title": ["invalid"],
                },
            )
        self.assertEqual(len(self.box.peers()["bindings"]), 1)

    def test_empty_explicit_id_never_fetches_another_message(self):
        mid = self.message()
        for bad in ("", False, 0):
            with self.subTest(value=bad), self.assertRaises(ValueError):
                self.box.receive("zcode", "audit-A", bad)
        self.assertEqual(self.box.status(mid)["state"], "queued")

    def test_null_explicit_inbox_id_is_rejected_by_mcp(self):
        mid = self.message()
        with self.assertRaises(ValueError):
            invoke(
                self.box,
                "zcode",
                "bridge_inbox",
                {
                    "caller_session_id": "audit-A",
                    "message_id": None,
                },
            )
        self.assertEqual(self.box.status(mid)["state"], "queued")

    def test_inbox_delivery_timestamp_matches_persisted_transition(self):
        mid = self.message()
        first = self.box.receive("zcode", "audit-A", mid)[0]
        self.assertIsNotNone(first["delivered_at"])
        self.assertEqual(first["delivered_at"], self.box.status(mid)["delivered_at"])
        self.assertEqual(
            first["delivered_at"],
            self.box.receive("zcode", "audit-A", mid)[0]["delivered_at"],
        )

    def test_sparse_lifecycle_hook_preserves_known_workspace_and_title(self):
        process(
            self.box,
            "zcode",
            {
                "session_id": "audit-A",
                "hook_event_name": "UserPromptSubmit",
                "prompt": "ordinary",
            },
        )
        peer = next(
            p for p in self.box.peers()["peers"] if p["session_id"] == "audit-A"
        )
        self.assertEqual(peer["cwd"], "E:/audit/A")
        self.assertEqual(peer["title"], "Audit A")
        self.box.register("zcode", "audit-A", "Renamed", "E:/audit/new")
        peer = next(
            p for p in self.box.peers()["peers"] if p["session_id"] == "audit-A"
        )
        self.assertEqual(
            (peer["title"], peer["cwd"], peer["guard_ready"]),
            ("Renamed", "E:/audit/new", 1),
        )

    def test_safe_rearm_applies_new_options_and_discards_stale_retry_proof(self):
        mid = self.job(priority=False, allow_busy_navigation=False)
        queue.run_one(self.box, deliver=self.failure)
        with self.box.connect() as db:
            db.execute(
                "UPDATE dispatch_jobs SET state='exhausted' WHERE message_id=?", (mid,)
            )
        result = queue.enqueue(
            self.box, mid, priority=True, allow_busy_navigation=True, start=False
        )
        job = result["dispatch_queue"]
        self.assertEqual((job["priority"], job["allow_busy_navigation"]), (1, 1))
        self.assertEqual(job["attempts"], 0)
        self.assertEqual(job["last_delivery"], {})

    def test_backoff_starts_after_slow_attempt_finishes(self):
        mid = self.job()
        started = time.time()
        finished = started + 20
        with patch.object(queue.time, "time", return_value=finished):
            queue.run_one(self.box, deliver=self.failure, stamp=started)
        self.assertEqual(
            queue.queue_status(self.box, mid)["next_attempt_at"],
            finished + queue.BACKOFF[0],
        )

    def test_expired_job_with_real_ack_is_cancelled_not_failed(self):
        mid = self.job()
        self.ack(mid)
        with self.box.connect() as db:
            db.execute(
                "UPDATE dispatch_jobs SET created_at=0 WHERE message_id=?", (mid,)
            )
        deliver = Mock()
        queue.run_one(self.box, deliver=deliver)
        deliver.assert_not_called()
        self.assertEqual(queue.queue_status(self.box, mid)["state"], "cancelled")

    def test_old_transport_error_is_explicitly_historical_after_result(self):
        mid = self.job()
        queue.run_one(self.box, deliver=self.failure)
        with self.box.connect() as db:
            db.execute(
                "UPDATE dispatch_jobs SET state='held' WHERE message_id=?", (mid,)
            )
        self.ack(mid)
        self.box.reply("zcode", "audit-A", mid, "FIXTURE_DONE")
        job = self.box.status(mid)["dispatch_queue"]
        self.assertTrue(job["receipt_confirmed"])
        self.assertTrue(job["error_is_historical"])
        self.assertEqual(job["state"], "held")
        self.assertEqual(job["last_error"], "Connection timed out")
        self.assertIn("Do not redispatch", job["next_action"])

    def test_crashed_retry_cannot_reuse_older_safe_failure_proof(self):
        mid = self.job()
        queue.run_one(self.box, deliver=self.failure)
        with self.box.connect() as db:
            db.execute(
                "UPDATE dispatch_jobs SET state='working' WHERE message_id=?", (mid,)
            )
        queue.recover_working(self.box)
        result = queue.enqueue(self.box, mid, start=False)
        self.assertEqual(result["dispatch_queue"]["state"], "held")


if __name__ == "__main__":
    unittest.main()
