"""Fallback eligibility and mailbox identity, without contacting a desktop."""

import contextlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import cdp_delivery as cdp
from cu_client import DriverError
from test_cdp_delivery import FakeClient

from bridge import Mailbox


class NavigationTests(unittest.TestCase):
    def setUp(self):
        self.binding = {
            "harness": "zcode",
            "session_id": "fixture",
            "title": "Fixture",
            "workspace_path": "E:/fixture",
        }
        self.client = FakeClient("zcode")
        self.client.matches = False

    def route(self, allow_busy_navigation=False):
        with (
            tempfile.TemporaryDirectory() as folder,
            patch.object(cdp, "ROOT", Path(folder)),
            patch("desktop_delivery.desktop_lock", contextlib.nullcontext),
        ):
            return cdp.deliver_background(
                self.binding,
                "msg_" + "a" * 32,
                lambda harness: self.client,
                allow_busy_navigation=allow_busy_navigation,
            )

    def observation(self, rows):
        original = self.client.evaluate

        def evaluate(op, binding, marker):
            if op.startswith("select"):
                return {
                    "error": "bound_chat_not_unique_or_not_visible",
                    "target_rows": rows,
                }
            return original(op, binding, marker)

        self.client.evaluate = evaluate

    def test_missing_row_can_select_only_verified_zcode_target(self):
        self.observation(0)

        def select(client, binding, **options):
            self.assertIs(client.client, self.client)
            self.assertEqual(binding, self.binding)
            self.assertTrue(options["allow_busy_navigation"])
            self.client.matches = True
            return {"selected": True}

        with patch(
            "session_navigation.select_zcode_session", side_effect=select
        ) as native:
            result = self.route(allow_busy_navigation=True)
        native.assert_called_once()
        self.assertTrue(result["submitted"])
        self.assertEqual(self.client.submits, 1)

    def test_duplicate_rows_never_invoke_native_fallback(self):
        self.observation(2)
        with patch("session_navigation.select_zcode_session") as native:
            with self.assertRaises(DriverError):
                self.route()
        native.assert_not_called()
        self.assertEqual(self.client.commands, [])

    def test_context_replaced_after_selection_is_observed_without_second_selection(
        self,
    ):
        self.observation(0)

        def select(*args, **kwargs):
            self.client.matches = True
            failure = DriverError("DevTools renderer context changed")
            failure.context_changed = True
            raise failure

        with patch(
            "session_navigation.select_zcode_session", side_effect=select
        ) as native:
            result = self.route(allow_busy_navigation=True)
        native.assert_called_once()
        self.assertTrue(result["submitted"])
        self.assertEqual(self.client.submits, 1)

    def test_context_replacement_cannot_bypass_final_identity(self):
        self.observation(0)
        failure = DriverError("DevTools renderer context changed")
        failure.context_changed = True
        with (
            patch(
                "session_navigation.select_zcode_session", side_effect=failure
            ) as native,
            patch.object(cdp.time, "monotonic", side_effect=[0, 1, 16]),
            patch.object(cdp.time, "sleep"),
        ):
            with self.assertRaises(DriverError):
                self.route(allow_busy_navigation=True)
        native.assert_called_once()
        self.assertEqual(self.client.commands, [])

    def test_cdp_classifies_only_known_context_errors_without_replay(self):
        for message, changed in (
            ("Execution context was destroyed.", True),
            ("PRIVATE_RENDERER_ERROR", False),
        ):
            with self.subTest(changed=changed):
                client = object.__new__(cdp.CdpClient)
                client.sequence = 0
                client.socket = Mock()
                client.socket.recv.return_value = json.dumps(
                    {"id": 1, "error": {"code": -32000, "message": message}}
                )
                with self.assertRaises(DriverError) as failed:
                    client.call("Runtime.evaluate", {})
                self.assertEqual(
                    getattr(failed.exception, "context_changed", False), changed
                )
                self.assertNotIn("PRIVATE_RENDERER_ERROR", str(failed.exception))
                client.socket.send.assert_called_once()

    def test_draft_failure_never_invoke_native_fallback(self):
        self.client.draft = True
        with patch("session_navigation.select_zcode_session") as native:
            with self.assertRaisesRegex(DriverError, "existing_draft"):
                self.route()
        native.assert_not_called()
        self.assertEqual(self.client.commands, [])

    def test_dispatch_supplies_registered_workspace_for_frozen_target(self):
        with tempfile.TemporaryDirectory() as folder:
            box = Mailbox(Path(folder) / "fixture.db")
            box.register("zcode", "fixture", "Fixture", "E:/fixture", True)
            box.bind("test", "zcode", "fixture")
            mid = box.send("test", "fixture")["id"]
            with patch(
                "cdp_delivery.deliver_background", return_value={"submitted": True}
            ) as send:
                box.dispatch(mid, allow_busy_navigation=True)
            self.assertEqual(send.call_args.args[0]["workspace_path"], "E:/fixture")
            self.assertEqual(send.call_args.args[0]["session_id"], "fixture")


if __name__ == "__main__":
    unittest.main()
