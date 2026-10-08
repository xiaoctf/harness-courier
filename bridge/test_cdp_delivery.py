import contextlib
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import cdp_delivery as cdp
from cu_client import DriverError
from wake_protocol import wake_text

MID = "msg_" + "a" * 32
BINDING = {"harness": "kimi", "session_id": "session_fixture", "title": "Fixture"}


class FakeClient:
    def __init__(self, harness):
        self.commands = []
        self.submits = 0
        self.error = None
        self.matches = True
        self.draft = False
        self.drop_submit = False
        self.target_count = 1

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def targets(self):
        return [{"id": str(i)} for i in range(self.target_count)]

    def attach(self, target):
        pass

    def call(self, name, args):
        self.commands.append((name, args))
        if name == "Input.dispatchKeyEvent" and args["type"] == "keyDown":
            self.submits += 1
            if self.drop_submit:
                raise DriverError("Response lost after submit")
        return {}

    def evaluate(self, op, binding, marker):
        if op == "observe":
            return {
                "matches": self.matches,
                "editor_count": 1,
                "draft_empty": not self.draft,
            }
        if op == "select":
            return {
                "error": "existing_draft"
                if self.draft
                else "bound_chat_not_unique_or_not_visible"
            }
        if op == "focus":
            return {"error": "existing_draft"} if self.draft else {"focused": True}
        if op == "ready":
            return {"error": self.error} if self.error else {"ready": True}
        if op == "keyboard_submit_ready":
            return {"error": self.error} if self.error else {"ready": True}
        if op == "submit":
            self.submits += 1
            if self.drop_submit:
                raise DriverError("Response lost after submit")
            return {"submitted": True}


class DeliveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.rootpatch = patch.object(cdp, "ROOT", Path(self.temp.name))
        self.rootpatch.start()
        self.lockpatch = patch("desktop_delivery.desktop_lock", contextlib.nullcontext)
        self.lockpatch.start()
        self.client = FakeClient("kimi")

    def tearDown(self):
        self.lockpatch.stop()
        self.rootpatch.stop()
        self.temp.cleanup()

    def send(self, binding=BINDING, mid=MID):
        return cdp.deliver_background(binding, mid, lambda h: self.client)

    def test_only_marker_is_sent_and_receipt_is_not_fabricated(self):
        result = self.send()
        self.assertEqual(
            self.client.commands[0],
            (
                "Input.insertText",
                {"text": wake_text(MID, "kimi", BINDING["session_id"])},
            ),
        )
        self.assertEqual(
            [c[0] for c in self.client.commands],
            ["Input.insertText", "Input.dispatchKeyEvent", "Input.dispatchKeyEvent"],
        )
        self.assertEqual(result["delivery_state"], "SUBMITTED_UNCONFIRMED")
        self.assertTrue(result["submitted"])
        self.assertFalse(result["receiver_verified"])

    def test_existing_draft_is_preserved(self):
        self.client.draft = True
        with self.assertRaisesRegex(DriverError, "existing_draft"):
            self.send()
        self.assertEqual(self.client.commands, [])
        self.assertEqual(self.client.submits, 0)

    def test_wrong_session_cannot_send(self):
        self.client.matches = False
        with self.assertRaises(DriverError):
            self.send()
        self.assertEqual(self.client.commands, [])

    def test_busy_other_conversation_stops_before_input(self):
        self.client.matches = False
        original = self.client.evaluate

        def evaluate(op, binding, marker):
            if op == "select":
                return {"error": "current_conversation_generating"}
            return original(op, binding, marker)

        with patch.object(self.client, "evaluate", side_effect=evaluate):
            with self.assertRaisesRegex(DriverError, "current_conversation_generating"):
                self.send()
        self.assertEqual(self.client.commands, [])
        self.assertEqual(self.client.submits, 0)

    def test_fresh_busy_guard_precedes_route_click(self):
        # Structural smoke check only; verify_cdp_fixture exercises the real DOM.
        # Do not bind this assertion to minified whitespace or variable names.
        select = cdp.DOM_FUNCTION
        self.assertLess(
            select.index("current_conversation_generating"),
            select.index("rows[0].click()"),
        )
        self.assertIn("button.stop", select)
        self.assertIn("v4-stop", select)

    def test_authorized_busy_navigation_still_verifies_target_before_typing(self):
        original = self.client.evaluate
        observations = iter([False, True, True])
        seen = []

        def evaluate(op, binding, marker):
            seen.append(op)
            if op == "select_authorized_busy_navigation":
                return {"selected": True}
            if op == "observe":
                return {
                    "matches": next(observations, True),
                    "editor_count": 1,
                    "draft_empty": True,
                }
            return original(op, binding, marker)

        with patch.object(self.client, "evaluate", side_effect=evaluate):
            cdp.deliver_background(
                BINDING, MID, lambda h: self.client, allow_busy_navigation=True
            )
        self.assertIn("select_authorized_busy_navigation", seen)
        self.assertEqual(self.client.submits, 1)

    def test_authorized_busy_navigation_cannot_override_draft(self):
        self.client.matches = False
        self.client.draft = True
        original = self.client.evaluate

        def evaluate(op, binding, marker):
            if op == "select_authorized_busy_navigation":
                return {"error": "existing_draft"}
            return original(op, binding, marker)

        with patch.object(self.client, "evaluate", side_effect=evaluate):
            with self.assertRaisesRegex(DriverError, "existing_draft"):
                cdp.deliver_background(
                    BINDING, MID, lambda h: self.client, allow_busy_navigation=True
                )
        self.assertEqual(self.client.commands, [])

    def test_route_error_has_bounded_metadata_not_draft_text(self):
        with self.assertRaisesRegex(DriverError, "route=.*editor_count") as caught:
            cdp.require(
                {
                    "matches": False,
                    "sid": "other",
                    "editor_count": 1,
                    "draft_empty": False,
                    "draft": "PRIVATE_TEXT",
                },
                "matches",
            )
        self.assertNotIn("PRIVATE_TEXT", str(caught.exception))

    def test_slow_route_mount_is_checked_before_typing(self):
        original = self.client.evaluate
        observations = iter([False, False, False, True, True])

        def evaluate(op, binding, marker):
            if op == "select":
                return {"selected": True}
            if op == "observe":
                return {
                    "matches": next(observations, True),
                    "editor_count": 1,
                    "draft_empty": True,
                }
            return original(op, binding, marker)

        with (
            patch.object(self.client, "evaluate", side_effect=evaluate),
            patch.object(cdp.time, "sleep"),
            patch.object(cdp.time, "monotonic", side_effect=[0, 1, 4, 8, 9, 9]),
        ):
            self.send()
        self.assertEqual(self.client.submits, 1)

    def test_route_timeout_never_types_or_submits(self):
        def evaluate(op, binding, marker):
            if op == "select":
                return {"selected": True}
            return {
                "matches": False,
                "sid": "other",
                "editor_count": 1,
                "draft_empty": True,
            }

        with (
            patch.object(self.client, "evaluate", side_effect=evaluate),
            patch.object(cdp.time, "sleep"),
            patch.object(cdp.time, "monotonic", side_effect=[0, 1, 16]),
        ):
            with self.assertRaisesRegex(DriverError, "matches not verified.*route="):
                self.send()
        self.assertEqual(self.client.commands, [])
        self.assertEqual(self.client.submits, 0)

    def test_ambiguous_windows_cannot_send(self):
        self.client.target_count = 2
        with self.assertRaisesRegex(DriverError, "Multiple"):
            self.send()
        self.assertEqual(self.client.commands, [])

    def test_draft_changed_after_input_prevents_submission(self):
        self.client.error = "marker_or_draft_changed"
        with patch.object(cdp.time, "sleep"):
            self.client.evaluate_orig = self.client.evaluate
            with patch.object(cdp.time, "monotonic", side_effect=[0, 3]):
                with self.assertRaisesRegex(DriverError, "marker_or_draft_changed"):
                    self.send()
        self.assertEqual(self.client.submits, 0)

    def test_duplicate_submission_is_prevented(self):
        self.send()
        self.send()
        self.assertEqual(self.client.submits, 1)
        self.assertEqual(len(self.client.commands), 3)

    def recovery(self, state=None, sha=None):
        journal = cdp.ROOT / "data" / "background-delivery" / (MID + ".json")
        state = state or {
            "id": MID,
            "state": "queued",
            "delivered_at": None,
            "acknowledged_at": None,
            "session_id": BINDING["session_id"],
            "harness": "kimi",
            "binding_revision": None,
        }
        return cdp.deliver_background(
            BINDING,
            MID,
            lambda h: self.client,
            recovery_sha=sha or hashlib.sha256(journal.read_bytes()).hexdigest(),
            recovery_status=lambda: state,
        )

    def test_explicit_recovery_preserves_original_and_is_once_only(self):
        self.send()
        journal = cdp.ROOT / "data" / "background-delivery" / (MID + ".json")
        before = journal.read_bytes()
        result = self.recovery()
        self.assertTrue(result["recovery"])
        self.assertEqual(self.client.submits, 2)
        self.assertEqual(journal.read_bytes(), before)
        with self.assertRaisesRegex(DriverError, "already attempted"):
            self.recovery()
        self.assertEqual(self.client.submits, 2)

    def test_task_body_recovery_requires_explicit_guard(self):
        with self.assertRaisesRegex(DriverError, "explicit unreceived"):
            cdp.deliver_background(
                BINDING, MID, lambda h: self.client, recovery_body="current task"
            )
        self.assertEqual(self.client.commands, [])

    def test_staged_recovery_only_resumes_before_any_submit(self):
        self.send()
        journal = cdp.ROOT / "data" / "background-delivery" / (MID + ".json")
        sha = hashlib.sha256(journal.read_bytes()).hexdigest()
        reserved = journal.with_name(MID + ".recovery-1.json")
        reserved.write_text(
            json.dumps(
                {
                    "phase": "recovery_reserved",
                    "sid": BINDING["session_id"],
                    "harness": "kimi",
                    "original_journal_sha256": sha,
                }
            )
        )
        state = {
            "id": MID,
            "state": "queued",
            "delivered_at": None,
            "acknowledged_at": None,
            "session_id": BINDING["session_id"],
            "harness": "kimi",
            "binding_revision": None,
        }
        before = len(self.client.commands)
        cdp.deliver_background(
            BINDING,
            MID,
            lambda h: self.client,
            recovery_sha=sha,
            recovery_status=lambda: state,
            recovery_body="Same task body",
            resume_staged=True,
        )
        self.assertTrue(
            all(c[0] == "Input.dispatchKeyEvent" for c in self.client.commands[before:])
        )
        with self.assertRaisesRegex(DriverError, "already attempted"):
            cdp.deliver_background(
                BINDING,
                MID,
                lambda h: self.client,
                recovery_sha=sha,
                recovery_status=lambda: state,
                recovery_body="Same task body",
                resume_staged=True,
            )

    def test_task_body_same_id_recovery_retains_journal_and_no_reserved_prefix(self):
        self.send()
        journal = cdp.ROOT / "data" / "background-delivery" / (MID + ".json")
        before = journal.read_bytes()
        state = {
            "id": MID,
            "state": "queued",
            "delivered_at": None,
            "acknowledged_at": None,
            "session_id": BINDING["session_id"],
            "harness": "kimi",
            "binding_revision": None,
        }
        body = f"Same message {MID}: read exact inbox then perform current task"
        r = cdp.deliver_background(
            BINDING,
            MID,
            lambda h: self.client,
            recovery_sha=hashlib.sha256(before).hexdigest(),
            recovery_status=lambda: state,
            recovery_body=body,
        )
        self.assertEqual(r["payload_mode"], "same_id_task_body")
        self.assertEqual(self.client.commands[-3], ("Input.insertText", {"text": body}))
        self.assertEqual(journal.read_bytes(), before)
        self.assertFalse(r["receiver_verified"])

    def test_recovery_rejects_received_ack_and_wrong_recipient(self):
        self.send()
        base = {
            "id": MID,
            "state": "queued",
            "delivered_at": None,
            "acknowledged_at": None,
            "session_id": BINDING["session_id"],
            "harness": "kimi",
            "binding_revision": None,
        }
        for delta in (
            {"state": "acknowledged"},
            {"delivered_at": 1},
            {"acknowledged_at": 1},
            {"session_id": "session_other"},
            {"binding_revision": 99},
        ):
            with self.subTest(delta=delta), self.assertRaises(DriverError):
                self.recovery({**base, **delta})
        self.assertEqual(self.client.submits, 1)

    def test_recovery_rejects_wrong_sha_and_draft(self):
        self.send()
        with self.assertRaisesRegex(DriverError, "SHA mismatch"):
            self.recovery(sha="0" * 64)
        self.client.draft = True
        with self.assertRaisesRegex(DriverError, "existing_draft"):
            self.recovery()
        self.assertEqual(self.client.submits, 1)

    def test_recovery_lost_response_cannot_repeat(self):
        self.send()
        self.client.drop_submit = True
        with self.assertRaises(DriverError):
            self.recovery()
        with self.assertRaisesRegex(DriverError, "already attempted"):
            self.recovery()
        self.assertEqual(self.client.submits, 2)

    def test_recovery_rechecks_status_before_typing(self):
        self.send()
        journal = cdp.ROOT / "data" / "background-delivery" / (MID + ".json")
        queued = {
            "id": MID,
            "state": "queued",
            "delivered_at": None,
            "acknowledged_at": None,
            "session_id": BINDING["session_id"],
            "harness": "kimi",
            "binding_revision": None,
        }
        states = iter([queued, {**queued, "state": "acknowledged"}])
        with self.assertRaises(DriverError):
            cdp.deliver_background(
                BINDING,
                MID,
                lambda h: self.client,
                recovery_sha=hashlib.sha256(journal.read_bytes()).hexdigest(),
                recovery_status=lambda: next(states),
            )
        self.assertEqual(self.client.submits, 1)
        self.assertEqual(len(self.client.commands), 3)

    def test_click_without_composer_clear_is_not_confirmed(self):
        original = self.client.evaluate

        def evaluate(op, binding, marker):
            r = original(op, binding, marker)
            if op == "observe" and self.client.submits:
                r["draft_empty"] = False
            return r

        with (
            patch.object(self.client, "evaluate", side_effect=evaluate),
            patch.object(cdp.time, "monotonic", side_effect=[0, 0, 0, 4]),
        ):
            with self.assertRaisesRegex(DriverError, "outcome uncertain"):
                self.send()
        journal = cdp.ROOT / "data" / "background-delivery" / (MID + ".json")
        self.assertEqual(json.loads(journal.read_text())["phase"], "submit_attempt")

    def test_response_loss_never_causes_resubmit(self):
        self.client.drop_submit = True
        with self.assertRaises(DriverError):
            self.send()
        with self.assertRaisesRegex(DriverError, "uncertain"):
            self.send()
        self.assertEqual(self.client.submits, 1)

    def test_journal_target_change_is_rejected(self):
        self.send()
        with self.assertRaisesRegex(DriverError, "target mismatch"):
            self.send({**BINDING, "session_id": "session_other"})

    def test_invalid_marker_is_rejected_before_connect(self):
        with self.assertRaises(DriverError):
            self.send(mid="../../unsafe")
        self.assertEqual(self.client.commands, [])

    def test_production_rejects_ordinary_browser_tabs(self):
        self.assertFalse(
            cdp.main_page(
                "kimi",
                {"type": "page", "url": "https://example.com/sessions/session_fixture"},
            )
        )
        self.assertFalse(
            cdp.main_page(
                "zcode", {"type": "page", "url": "file:///E:/fake/index.html"}
            )
        )
        self.assertTrue(
            cdp.main_page(
                "kimi",
                {"type": "page", "url": "app://renderer/sessions/session_fixture"},
            )
        )
        self.assertFalse(
            cdp.main_page(
                "kimi", {"type": "page", "url": "app://renderer/browser-overlay.html"}
            )
        )

    def test_foreground_cdp_methods_are_not_admitted(self):
        client = object.__new__(cdp.CdpClient)
        with self.assertRaisesRegex(DriverError, "outside"):
            client.call("Page.bringToFront", {})

    def test_arbitrary_keyboard_input_is_not_admitted(self):
        client = object.__new__(cdp.CdpClient)
        with self.assertRaisesRegex(DriverError, "Only the verified"):
            client.call("Input.dispatchKeyEvent", {"type": "keyDown", "key": "a"})

    def test_kimi_focus_loss_blocks_keyboard_submit(self):
        original = self.client.evaluate

        def evaluate(op, binding, marker):
            if op == "keyboard_submit_ready":
                return {"error": "composer_focus_changed"}
            return original(op, binding, marker)

        with patch.object(self.client, "evaluate", side_effect=evaluate):
            with self.assertRaisesRegex(DriverError, "composer_focus_changed"):
                self.send()
        self.assertEqual(self.client.submits, 0)

    def test_endpoint_owner_mismatch_is_rejected(self):
        import psutil

        listener = SimpleNamespace(
            status=psutil.CONN_LISTEN,
            laddr=SimpleNamespace(port=49371, ip="127.0.0.1"),
            pid=5,
        )
        with (
            patch("psutil.net_connections", return_value=[listener]),
            patch(
                "psutil.Process",
                return_value=SimpleNamespace(exe=lambda: r"C:\foreign.exe"),
            ),
        ):
            with self.assertRaisesRegex(DriverError, "another executable"):
                cdp.endpoint_owner("kimi")

    def test_public_listener_is_rejected(self):
        import psutil

        listener = SimpleNamespace(
            status=psutil.CONN_LISTEN,
            laddr=SimpleNamespace(port=49371, ip="0.0.0.0"),
            pid=5,
        )
        with patch("psutil.net_connections", return_value=[listener]):
            with self.assertRaisesRegex(DriverError, "loopback"):
                cdp.endpoint_owner("kimi")


if __name__ == "__main__":
    unittest.main(verbosity=2)
