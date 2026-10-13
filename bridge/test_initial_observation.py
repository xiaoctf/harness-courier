"""Bounded reconnection is limited to the first read before any mutation."""

import contextlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import cdp_delivery as cdp
from cu_client import DriverError
from test_cdp_delivery import BINDING, MID, FakeClient
from websocket import WebSocketTimeoutException


class InitialObservationTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        self.client = FakeClient("kimi")

    def deliver(self, **options):
        with (
            patch.object(cdp, "ROOT", self.root),
            patch("desktop_delivery.desktop_lock", contextlib.nullcontext),
        ):
            return cdp.deliver_background(
                BINDING, MID, lambda _: self.client, **options
            )

    def fail_first_observation(self):
        original = self.client.evaluate
        failed = False

        def evaluate(op, *args):
            nonlocal failed
            if op == "observe" and not failed:
                failed = True
                raise WebSocketTimeoutException("PRIVATE")
            return original(op, *args)

        self.client.evaluate = Mock(side_effect=evaluate)

    def test_initial_timeout_reconnects_same_renderer_then_inserts_once(self):
        self.fail_first_observation()
        self.client.attach = Mock()
        self.assertTrue(self.deliver()["submitted"])
        self.assertEqual(self.client.attach.call_count, 3)
        self.assertEqual(self.client.submits, 1)
        self.assertEqual(
            [method for method, _ in self.client.commands].count("Input.insertText"), 1
        )

    def test_second_read_timeout_stops_without_input_or_journal(self):
        self.client.evaluate = Mock(side_effect=WebSocketTimeoutException("PRIVATE"))
        self.client.attach = Mock()
        with self.assertRaises(WebSocketTimeoutException) as caught:
            self.deliver()
        self.assertTrue(caught.exception.retry_safe)
        self.assertEqual(self.client.evaluate.call_count, 2)
        self.assertEqual(self.client.attach.call_count, 2)
        self.assertEqual(self.client.commands, [])
        self.assertFalse(
            (self.root / "data/background-delivery" / f"{MID}.json").exists()
        )

    def test_changed_renderer_is_rejected_without_input(self):
        self.fail_first_observation()
        self.client.targets = Mock(side_effect=[[{"id": "0"}], [{"id": "changed"}]])
        with self.assertRaisesRegex(DriverError, "original renderer"):
            self.deliver()
        self.assertEqual(self.client.commands, [])

    def test_initial_handshake_timeout_is_reconnected_once(self):
        self.client.attach = Mock(side_effect=[WebSocketTimeoutException(), None, None])
        self.assertTrue(self.deliver()["submitted"])
        self.assertEqual(self.client.attach.call_count, 3)
        self.assertEqual(self.client.submits, 1)

    def test_missing_original_renderer_id_stops_without_input(self):
        self.fail_first_observation()
        self.client.targets = Mock(return_value=[{}])
        with self.assertRaisesRegex(DriverError, "original renderer"):
            self.deliver()
        self.assertEqual(self.client.commands, [])

    def test_duplicate_original_renderer_id_stops_without_input(self):
        self.fail_first_observation()
        self.client.targets = Mock(
            side_effect=[[{"id": "0"}], [{"id": "0"}, {"id": "0"}]]
        )
        with self.assertRaisesRegex(DriverError, "original renderer"):
            self.deliver()
        self.assertEqual(self.client.commands, [])

    def test_selection_timeout_is_not_replayed(self):
        self.client.matches = False
        original = self.client.evaluate

        def evaluate(op, *args):
            if op == "select":
                raise WebSocketTimeoutException("PRIVATE")
            return original(op, *args)

        self.client.evaluate = Mock(side_effect=evaluate)
        self.client.targets = Mock(return_value=[{"id": "0"}])
        with self.assertRaises(WebSocketTimeoutException):
            self.deliver()
        self.client.targets.assert_called_once()
        self.assertEqual(self.client.commands, [])

    def test_post_selection_observe_timeout_recovers_without_selecting_twice(self):
        self.client.matches = False
        original = self.client.evaluate
        calls = []
        failed = False

        def evaluate(op, *args):
            nonlocal failed
            calls.append(op)
            if op == "select":
                self.client.matches = True
                return {"selected": True}
            if op == "observe" and self.client.matches and not failed:
                failed = True
                raise WebSocketTimeoutException("PRIVATE")
            return original(op, *args)

        self.client.evaluate = evaluate
        self.assertTrue(self.deliver()["submitted"])
        self.assertEqual(calls.count("select"), 1)
        self.assertEqual(self.client.submits, 1)

    def test_priority_observe_uses_remaining_shared_read_budget(self):
        original = self.client.evaluate
        reads = 0

        def evaluate(op, *args):
            nonlocal reads
            if op == "observe":
                reads += 1
                if reads in (1, 3):
                    raise WebSocketTimeoutException("PRIVATE")
            return original(op, *args)

        self.client.evaluate = evaluate
        with self.assertRaises(WebSocketTimeoutException) as caught:
            self.deliver(priority=True)
        self.assertTrue(caught.exception.retry_safe)
        self.assertEqual(caught.exception.failure_stage, "priority_observation")
        self.assertEqual(caught.exception.read_reconnects, 1)
        self.assertEqual(reads, 3)
        self.assertEqual(self.client.commands, [])

    def test_post_submit_timeout_never_reconnects_or_resubmits(self):
        original = self.client.evaluate

        def evaluate(op, *args):
            if op == "observe" and self.client.submits:
                raise WebSocketTimeoutException("PRIVATE")
            return original(op, *args)

        self.client.evaluate = evaluate
        self.client.targets = Mock(return_value=[{"id": "0"}])
        with self.assertRaises(WebSocketTimeoutException) as caught:
            self.deliver()
        self.assertFalse(caught.exception.retry_safe)
        self.assertEqual(caught.exception.failure_stage, "post_submit_observation")
        self.client.targets.assert_called_once()
        self.assertEqual(self.client.submits, 1)

    def test_existing_unreadable_journal_never_claims_safe_retry(self):
        import json

        journal = self.root / "data/background-delivery" / (MID + ".json")
        journal.parent.mkdir(parents=True)
        journal.write_text("{", encoding="utf-8")
        with self.assertRaises(json.JSONDecodeError) as caught:
            self.deliver()
        self.assertFalse(caught.exception.retry_safe)
        self.assertEqual(journal.read_text(), "{")
        self.assertEqual(self.client.commands, [])


if __name__ == "__main__":
    unittest.main()
