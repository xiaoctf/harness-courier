"""Fetched mailbox-only messages must still admit a guarded first desktop wake."""

import contextlib
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import cdp_delivery as cdp
import dispatch_queue as queue
import mcp_dispatch
from test_cdp_delivery import FakeClient

from bridge import Mailbox


class FetchedDispatchTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.box = Mailbox(self.root / "mail.db")
        self.box.register(
            "kimi", "session_fixture", "Fixture", "fixture", guard_ready=True
        )
        self.box.bind("fixture", "kimi", "session_fixture")
        self.message = self.box.send("fixture", "fixture task", dispatch=False)
        self.mid = self.message["id"]
        self.box.receive("kimi", "session_fixture", self.mid)
        self.client = FakeClient("kimi")
        root_patch = patch.object(cdp, "ROOT", self.root)
        root_patch.start()
        self.addCleanup(root_patch.stop)
        queue_patch = patch.object(queue, "ROOT", self.root)
        queue_patch.start()
        self.addCleanup(queue_patch.stop)
        lock_patch = patch("desktop_delivery.desktop_lock", contextlib.nullcontext)
        lock_patch.start()
        self.addCleanup(lock_patch.stop)
        factory_patch = patch.object(cdp, "CdpClient", return_value=self.client)
        self.factory = factory_patch.start()
        self.addCleanup(factory_patch.stop)
        endpoint_patch = patch.object(
            cdp,
            "endpoint_owner",
            side_effect=AssertionError("Live endpoint forbidden in fixture"),
        )
        endpoint_patch.start()
        self.addCleanup(endpoint_patch.stop)
        original_delivery = cdp.deliver_background

        def fake_delivery(binding, message_id, client_factory=None, **options):
            return original_delivery(
                binding, message_id, client_factory or self.factory, **options
            )

        delivery_patch = patch.object(
            cdp, "deliver_background", side_effect=fake_delivery
        )
        delivery_patch.start()
        self.addCleanup(delivery_patch.stop)

    def test_fetched_without_error_gets_first_wake_but_not_ack(self):
        self.assertIsNone(self.box.status(self.mid)["dispatch_error"])
        result = self.box.dispatch(self.mid)
        self.assertTrue(result["desktop_delivery"]["submitted"])
        self.assertEqual(result["state"], "delivered")
        self.assertIsNone(result["acknowledged_at"])
        self.assertEqual(self.client.submits, 1)

    def test_completed_submission_journal_prevents_second_wake(self):
        self.box.dispatch(self.mid)
        result = self.box.dispatch(self.mid)
        self.assertTrue(result["desktop_delivery"]["duplicate_prevented"])
        self.assertEqual(self.client.submits, 1)
        self.factory.assert_called_once()

    def test_uncertain_journal_blocks_even_fetched_without_error(self):
        journal = self.root / "data" / "background-delivery" / (self.mid + ".json")
        journal.parent.mkdir(parents=True)
        journal.write_text(
            json.dumps(
                {"phase": "input_attempt", "sid": "session_fixture", "harness": "kimi"}
            ),
            encoding="utf-8",
        )
        result = self.box.dispatch(self.mid)
        self.assertFalse(result["desktop_delivery"]["submitted"])
        self.assertIn("uncertain", result["dispatch_error"])
        self.factory.assert_not_called()
        self.assertEqual(self.client.commands, [])

    def test_explicit_queue_dispatch_after_fetch_uses_same_id_once(self):
        queued = queue.enqueue(self.box, self.mid, start=False)
        self.assertEqual(queued["dispatch_queue"]["state"], "pending")
        self.assertTrue(queue.run_one(self.box))
        self.assertEqual(queue.queue_status(self.box, self.mid)["state"], "submitted")
        again = queue.enqueue(self.box, self.mid, start=False)
        self.assertEqual(again["id"], self.mid)
        self.assertFalse(queue.run_one(self.box))
        self.assertEqual(self.client.submits, 1)

    def test_fixed_child_admits_fetched_without_error(self):
        response = SimpleNamespace(
            returncode=0, stdout=json.dumps(self.box.status(self.mid)), stderr=""
        )
        with patch.object(
            mcp_dispatch.subprocess, "run", return_value=response
        ) as child:
            result = mcp_dispatch.dispatch_fresh(self.box, self.mid)
        self.assertEqual(result["id"], self.mid)
        child.assert_called_once()

    def test_ack_race_after_enqueue_cancels_without_input(self):
        queue.enqueue(self.box, self.mid, start=False)
        self.box.acknowledge("kimi", "session_fixture", self.mid)
        self.assertTrue(queue.run_one(self.box))
        self.assertEqual(queue.queue_status(self.box, self.mid)["state"], "cancelled")
        delivery = Mock()
        with patch.object(mcp_dispatch.subprocess, "run", delivery):
            mcp_dispatch.dispatch_fresh(self.box, self.mid)
        delivery.assert_not_called()
        self.factory.assert_not_called()
        self.assertEqual(self.box.dispatch(self.mid)["state"], "acknowledged")
        self.assertEqual(self.client.commands, [])


if __name__ == "__main__":
    unittest.main()
