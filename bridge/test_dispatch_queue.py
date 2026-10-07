"""Durable queue checks, including real competing Windows worker processes."""

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import cdp_delivery as cdp
import dispatch_queue as queue
from cu_client import DriverError
from test_cdp_delivery import BINDING, MID, FakeClient

from bridge import Mailbox, invoke

ROOT = Path(__file__).resolve().parent


class QueueTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.box = Mailbox(self.root / "queue.db")
        self.box.register(
            "kimi", "session_fixture", "Fixture", "fixture", guard_ready=True
        )
        self.box.bind("fixture", "kimi", "session_fixture")
        self.messages = []

    def job(self, **options):
        message = self.box.send("fixture", "fixture text")
        queue.enqueue(self.box, message["id"], start=False, **options)
        self.messages.append(message)
        return message["id"]

    def success(self, mid, **options):
        return {**self.box.status(mid), "desktop_delivery": {"submitted": True}}

    def error(self, reason, safe):
        def dispatch(mid, **options):
            return {
                **self.box.status(mid),
                "dispatch_error": reason,
                "desktop_delivery": {
                    "submitted": False,
                    "reason": reason,
                    "retry_safe": safe,
                },
            }

        return dispatch

    def due(self):
        with self.box.connect() as db:
            db.execute("UPDATE dispatch_jobs SET next_attempt_at=0")

    def test_only_explicit_dispatch_jobs_are_drained(self):
        old = self.box.send("fixture", "old mailbox-only message")
        mid = self.job()
        dispatch = Mock(side_effect=self.success)
        queue.run_one(self.box, deliver=dispatch)
        self.assertEqual(dispatch.call_args.args[0], mid)
        self.assertIsNone(queue.queue_status(self.box, old["id"]))

    def test_mcp_dispatch_can_queue_registered_offline_chat_without_fallback(self):
        self.box.register("kimi", "session_fixture", "Fixture", "fixture", online=False)

        def enqueue(mid, **options):
            return queue.enqueue(self.box, mid, start=False, **options)

        message = invoke(
            self.box,
            "codex",
            "bridge_send",
            {"alias": "fixture", "body": "offline registered target"},
            dispatch_handler=enqueue,
        )
        self.assertEqual(message["session_id"], "session_fixture")
        self.assertEqual(message["dispatch_queue"]["state"], "pending")
        self.assertEqual(
            self.box.peers()["bindings"][0]["session_id"], "session_fixture"
        )

    def test_durable_options_and_idempotent_enqueue(self):
        mid = self.job(priority=False, allow_busy_navigation=False)
        reopened = Mailbox(self.box.path)
        queue.enqueue(reopened, mid, start=False)
        state = queue.queue_status(reopened, mid)
        self.assertEqual(state["priority"], 0)
        self.assertEqual(state["allow_busy_navigation"], 0)
        with reopened.connect() as db:
            self.assertEqual(
                db.execute("SELECT count(*) FROM dispatch_jobs").fetchone()[0], 1
            )

    def test_priority_then_fifo_and_held_target_does_not_block_next(self):
        normal = self.job(priority=False)
        first = self.job()
        second = self.job()
        order = []

        def dispatch(mid, **options):
            order.append(mid)
            return (
                self.error("existing_draft", True)(mid)
                if mid == first
                else self.success(mid)
            )

        while queue.run_one(self.box, deliver=dispatch):
            pass
        self.assertEqual(order, [first, second, normal])
        self.assertEqual(queue.queue_status(self.box, first)["state"], "held")

    def test_receiver_ack_cancels_queued_dispatch_without_replay(self):
        mid = self.job()
        self.box.receive("kimi", "session_fixture", mid)
        self.box.acknowledge("kimi", "session_fixture", mid)
        dispatch = Mock()
        queue.run_one(self.box, deliver=dispatch)
        dispatch.assert_not_called()
        self.assertEqual(queue.queue_status(self.box, mid)["state"], "cancelled")

    def test_submitted_transport_never_fabricates_receiver_state(self):
        mid = self.job()
        queue.run_one(self.box, deliver=self.success)
        state = self.box.status(mid)
        self.assertEqual(state["state"], "queued")
        self.assertIsNone(state["acknowledged_at"])
        self.assertEqual(state["dispatch_queue"]["state"], "submitted")

    def test_safe_transient_failure_backoff_then_same_id_success(self):
        mid = self.job()
        queue.run_one(self.box, deliver=self.error("Connection timed out", True))
        state = queue.queue_status(self.box, mid)
        self.assertEqual(state["state"], "pending")
        self.assertEqual(state["attempts"], 1)
        self.assertGreater(state["next_attempt_at"], time.time())
        self.assertFalse(queue.run_one(self.box, deliver=self.success))
        self.due()
        queue.run_one(self.box, deliver=self.success)
        self.assertEqual(queue.queue_status(self.box, mid)["attempts"], 2)

    def test_safe_retry_budget_is_bounded(self):
        mid = self.job()
        for _ in range(queue.MAX_ATTEMPTS):
            self.due()
            self.assertTrue(
                queue.run_one(
                    self.box, deliver=self.error("Connection timed out", True)
                )
            )
        self.assertEqual(queue.queue_status(self.box, mid)["state"], "exhausted")
        self.due()
        self.assertFalse(queue.run_one(self.box, deliver=self.success))

    def test_uncertain_post_input_failure_is_never_retried(self):
        for reason, safe in [
            ("Connection timed out", False),
            ("outcome uncertain", True),
            ("existing_draft", True),
            ("Binding changed", True),
        ]:
            with self.subTest(reason=reason):
                mid = self.job()
                queue.run_one(self.box, deliver=self.error(reason, safe))
                self.assertEqual(queue.queue_status(self.box, mid)["state"], "held")
        self.due()
        self.assertFalse(queue.run_one(self.box, deliver=self.success))

    def test_explicit_retry_of_safe_draft_hold_keeps_original_id(self):
        mid = self.job()
        queue.run_one(self.box, deliver=self.error("existing_draft", True))
        with patch.object(queue, "ROOT", self.root):
            queue.enqueue(self.box, mid, start=False)
        self.assertEqual(queue.queue_status(self.box, mid)["state"], "pending")
        queue.run_one(self.box, deliver=self.success)
        self.assertEqual(queue.queue_status(self.box, mid)["state"], "submitted")

    def test_uncertain_journal_blocks_explicit_rearm(self):
        mid = self.job()
        queue.run_one(self.box, deliver=self.error("existing_draft", True))
        journal = self.root / "data" / "background-delivery" / (mid + ".json")
        journal.parent.mkdir(parents=True)
        journal.write_text('{"phase":"input_attempt"}')
        with patch.object(queue, "ROOT", self.root):
            queue.enqueue(self.box, mid, start=False)
        self.assertEqual(queue.queue_status(self.box, mid)["state"], "held")

    def test_explicit_safe_exhausted_retry_starts_fresh_bounded_window(self):
        mid = self.job()
        for _ in range(queue.MAX_ATTEMPTS):
            self.due()
            queue.run_one(self.box, deliver=self.error("Connection timed out", True))
        with patch.object(queue, "ROOT", self.root):
            queue.enqueue(self.box, mid, start=False)
        job = queue.queue_status(self.box, mid)
        self.assertEqual((job["state"], job["attempts"]), ("pending", 0))
        queue.run_one(self.box, deliver=self.success)
        self.assertEqual(queue.queue_status(self.box, mid)["state"], "submitted")

    def test_expired_unattempted_job_can_be_explicitly_rearmed(self):
        mid = self.job()
        with self.box.connect() as db:
            db.execute("UPDATE dispatch_jobs SET created_at=0")
        dispatch = Mock(side_effect=self.success)
        queue.run_one(self.box, deliver=dispatch)
        dispatch.assert_not_called()
        self.assertEqual(queue.queue_status(self.box, mid)["state"], "exhausted")
        with patch.object(queue, "ROOT", self.root):
            queue.enqueue(self.box, mid, start=False)
        queue.run_one(self.box, deliver=dispatch)
        self.assertEqual(dispatch.call_count, 1)

    def test_binding_changed_after_enqueue_is_held_with_domain_reason(self):
        mid = self.job()
        self.box.register("kimi", "other_fixture", "Other", "fixture", guard_ready=True)
        self.box.bind("fixture", "kimi", "other_fixture", replace=True)
        queue.run_one(self.box)
        job = queue.queue_status(self.box, mid)
        self.assertEqual(job["state"], "held")
        self.assertIn("Binding changed", job["last_error"])
        self.assertEqual(self.box.status(mid)["session_id"], "session_fixture")

    def test_crashed_working_is_held_pending_survives_restart(self):
        crashed, pending = self.job(), self.job()
        with self.box.connect() as db:
            db.execute(
                "UPDATE dispatch_jobs SET state='working' WHERE message_id=?",
                (crashed,),
            )
        reopened = Mailbox(self.box.path)
        queue.recover_working(reopened)
        self.assertEqual(queue.queue_status(reopened, crashed)["state"], "held")
        self.assertEqual(queue.queue_status(reopened, pending)["state"], "pending")
        queue.run_one(reopened, deliver=self.success)
        self.assertEqual(queue.queue_status(reopened, pending)["state"], "submitted")

    def test_changed_binding_refuses_enqueue(self):
        message = self.box.send("fixture", "fixture")
        self.box.register("kimi", "other_fixture", "Other", "fixture", guard_ready=True)
        self.box.bind("fixture", "kimi", "other_fixture", replace=True)
        with self.assertRaisesRegex(ValueError, "Binding changed"):
            queue.enqueue(self.box, message["id"], start=False)

    def test_fixed_worker_command_has_no_caller_shell(self):
        self.job()
        with patch.object(
            queue.subprocess, "Popen", return_value=Mock(pid=1234)
        ) as spawn:
            self.assertEqual(queue.ensure_worker(self.box), 1234)
        self.assertEqual(
            spawn.call_args.args[0],
            [
                sys.executable,
                str(ROOT / "dispatch_worker.py"),
                "--db",
                str(self.box.path.resolve()),
            ],
        )
        self.assertIs(spawn.call_args.kwargs["shell"], False)

    def test_no_pending_work_does_not_spawn(self):
        with patch.object(queue.subprocess, "Popen") as spawn:
            self.assertIsNone(queue.ensure_worker(self.box))
        spawn.assert_not_called()

    @unittest.skipUnless(os.name == "nt", "Windows worker locking")
    def test_two_real_workers_serialize_and_deliver_each_job_once(self):
        mids = [self.job() for _ in range(8)]
        with self.box.connect() as db:
            db.execute(
                "CREATE TABLE fixture_events(seq INTEGER PRIMARY KEY,event TEXT,mid TEXT)"
            )
        code = """import sys,time
from bridge import Mailbox, invoke
import dispatch_queue as q
box=Mailbox(sys.argv[1])
def deliver(mid,**options):
 with box.connect() as db:db.execute("INSERT INTO fixture_events(event,mid) VALUES('start',?)",(mid,))
 time.sleep(.03)
 with box.connect() as db:db.execute("INSERT INTO fixture_events(event,mid) VALUES('end',?)",(mid,))
 return {**box.status(mid),'desktop_delivery':{'submitted':True}}
q.run_worker(box,deliver=deliver,idle_seconds=.1)
"""
        children = [
            subprocess.Popen(
                [sys.executable, "-c", code, str(self.box.path)],
                cwd=ROOT,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            for _ in range(2)
        ]
        for child in children:
            out, err = child.communicate(timeout=15)
            self.assertEqual(child.returncode, 0, err.decode())
        with self.box.connect() as db:
            events = db.execute(
                "SELECT event,mid FROM fixture_events ORDER BY seq"
            ).fetchall()
        self.assertEqual(len(events), 16)
        active, starts = set(), []
        for event, mid in events:
            if event == "start":
                self.assertFalse(active)
                active.add(mid)
                starts.append(mid)
            else:
                self.assertEqual(active, {mid})
                active.remove(mid)
        self.assertEqual(starts, mids)
        self.assertEqual(
            {queue.queue_status(self.box, mid)["state"] for mid in mids}, {"submitted"}
        )


class TransportRetryProofTests(unittest.TestCase):
    def test_pre_input_connection_failure_is_tagged(self):
        with (
            tempfile.TemporaryDirectory() as td,
            patch.object(cdp, "ROOT", Path(td)),
            patch(
                "desktop_delivery.desktop_lock", __import__("contextlib").nullcontext
            ),
        ):
            with self.assertRaises(DriverError) as failure:
                cdp.deliver_background(
                    BINDING, MID, Mock(side_effect=DriverError("Connection timed out"))
                )
        self.assertTrue(failure.exception.retry_safe)

    def test_post_input_connection_failure_is_not_retry_safe_and_has_journal(self):
        client = FakeClient("kimi")
        client.call = Mock(side_effect=DriverError("Connection timed out"))
        with (
            tempfile.TemporaryDirectory() as td,
            patch.object(cdp, "ROOT", Path(td)),
            patch(
                "desktop_delivery.desktop_lock", __import__("contextlib").nullcontext
            ),
        ):
            with self.assertRaises(DriverError) as failure:
                cdp.deliver_background(BINDING, MID, lambda h: client)
            journal = json.loads(
                (
                    Path(td) / "data" / "background-delivery" / (MID + ".json")
                ).read_text()
            )
            self.assertEqual(journal["phase"], "input_attempt")
        self.assertFalse(failure.exception.retry_safe)


if __name__ == "__main__":
    unittest.main()
