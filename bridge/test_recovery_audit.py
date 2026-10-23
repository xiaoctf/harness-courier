"""Recovery and observation regressions use only isolated mailboxes/configs."""

import contextlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import desktop_delivery
import dispatch_queue as queue
import install_integrations as installer
from agent_activity import inspect_target, wait_with_activity
from receiver_hook import process

from bridge import Mailbox


class RecoveryAuditTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.box = Mailbox(self.root / "audit.db")
        self.box.register("kimi", "audit-session", "Audit", "audit", True)
        self.box.bind("audit", "kimi", "audit-session")
        self.patcher = patch.object(queue, "ROOT", self.root)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)
        self.lock = patch.object(
            desktop_delivery, "desktop_lock", side_effect=contextlib.nullcontext
        )
        self.mock_lock = self.lock.start()
        self.addCleanup(self.lock.stop)

    def interrupted(self):
        mid = self.box.send("audit", "inert fixture")["id"]
        queue.enqueue(self.box, mid, start=False)
        with self.box.connect() as db:
            db.execute(
                "UPDATE dispatch_jobs SET state='working' WHERE message_id=?", (mid,)
            )
        queue.recover_working(self.box)
        return mid

    def test_explicit_interrupted_recovery_uses_lock_and_same_id(self):
        mid = self.interrupted()
        self.assertEqual(queue.queue_status(self.box, mid)["state"], "held")
        value = queue.enqueue(self.box, mid, start=False)
        self.assertEqual(value["id"], mid)
        self.assertEqual(value["dispatch_queue"]["state"], "pending")
        self.mock_lock.assert_called_once()

    def test_any_reserved_or_recovery_journal_prevents_rearm(self):
        for suffix in (".json", ".recovery-1.json"):
            with self.subTest(suffix=suffix):
                mid = self.interrupted()
                directory = self.root / "data/background-delivery"
                directory.mkdir(parents=True, exist_ok=True)
                (directory / (mid + suffix)).write_text("{}")
                self.assertEqual(
                    queue.enqueue(self.box, mid, start=False)["dispatch_queue"][
                        "state"
                    ],
                    "held",
                )

    def test_late_ack_during_lock_does_not_rearm(self):
        mid = self.interrupted()

        @contextlib.contextmanager
        def late_ack():
            self.box.receive("kimi", "audit-session", mid)
            self.box.acknowledge("kimi", "audit-session", mid)
            yield

        self.mock_lock.side_effect = late_ack
        value = queue.enqueue(self.box, mid, start=False)
        self.assertEqual(value["state"], "acknowledged")
        self.assertEqual(value["dispatch_queue"]["state"], "held")

    def test_rebind_during_lock_refuses_recovery(self):
        mid = self.interrupted()

        @contextlib.contextmanager
        def rebind():
            self.box.register("kimi", "replacement", "Other", "audit", True)
            self.box.bind("audit", "kimi", "replacement", replace=True)
            yield

        self.mock_lock.side_effect = rebind
        with self.assertRaisesRegex(ValueError, "Binding changed"):
            queue.enqueue(self.box, mid, start=False)

    def test_unknown_worker_failure_remains_held(self):
        mid = self.interrupted()
        with self.box.connect() as db:
            db.execute(
                "UPDATE dispatch_jobs SET last_error='Unknown failure' WHERE message_id=?",
                (mid,),
            )
        self.assertEqual(
            queue.enqueue(self.box, mid, start=False)["dispatch_queue"]["state"], "held"
        )
        self.mock_lock.assert_not_called()

    def test_status_reports_held_and_wait_returns_attention(self):
        mid = self.interrupted()
        native = {
            "available": True,
            "target_selected": True,
            "composer_verified": True,
            "generating": False,
        }
        value = inspect_target(
            self.box, self.box.binding("audit"), probe=lambda _: native
        )
        self.assertIn("dispatch_blocked", value["attention"])
        self.assertEqual(value["blocked_dispatches"][0]["id"], mid)
        self.assertEqual(
            wait_with_activity(self.box, mid, timeout=0)["wait_reason"],
            "attention_required",
        )

    def test_receipt_supersedes_held_alert(self):
        mid = self.interrupted()
        self.box.receive("kimi", "audit-session", mid)
        self.box.acknowledge("kimi", "audit-session", mid)
        value = inspect_target(
            self.box, self.box.binding("audit"), observe_native=False
        )
        self.assertNotIn("dispatch_blocked", value["attention"])

    def test_ordinary_hook_advances_without_replaying_delivered(self):
        old = self.box.send("audit", "old fixture")["id"]
        new = self.box.send("audit", "new fixture")["id"]
        payload = {
            "session_id": "audit-session",
            "hook_event_name": "UserPromptSubmit",
            "prompt": "ordinary fixture",
        }
        first, _ = process(self.box, "kimi", payload)
        second, _ = process(self.box, "kimi", payload)
        third, _ = process(self.box, "kimi", payload)
        self.assertIn(old, first["additional_context"])
        self.assertNotIn(old, second["additional_context"])
        self.assertIn(new, second["additional_context"])
        self.assertNotIn(new, third["additional_context"])
        self.assertEqual(self.box.status(old)["state"], "delivered")
        payload["prompt"] = f"[HARNESS_BRIDGE_WAKE:{old}]"
        exact, _ = process(self.box, "kimi", payload)
        self.assertIn(old, exact["additional_context"])

    def test_commentless_kimi_config_is_idempotent(self):
        home = self.root / "home"
        home.mkdir()
        python = Path("python.exe")
        for path, _, after, _ in installer.build_changes(home, python):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(after)
        config = home / ".kimi-code/config.toml"
        config.write_text(
            "\n".join(
                line
                for line in config.read_text().splitlines()
                if not line.startswith("#")
            ),
            encoding="utf-8",
        )
        for _, before, after, _ in installer.build_changes(home, python):
            self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
