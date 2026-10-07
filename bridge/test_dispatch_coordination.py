"""Concurrency and priority regressions; temporary mailboxes, no user chats."""

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import cdp_delivery as cdp
import desktop_delivery
import dispatch_background
import dispatch_queue
import mcp_dispatch
from cu_client import DriverError
from test_cdp_delivery import BINDING, MID, FakeClient

from bridge import Mailbox, invoke, tool_specs

ROOT = Path(__file__).resolve().parent


class CoordinationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.box = Mailbox(self.root / "mail.db")
        self.box.register(
            "kimi", "session_fixture", "Fixture", "fixture", guard_ready=True
        )
        self.box.bind("fixture", "kimi", "session_fixture")

    def test_many_senders_keep_unique_ids_and_original_targets(self):
        def send(index):
            return self.box.send(
                "fixture", str(index), sender_session_id=f"codex_{index}"
            )

        with ThreadPoolExecutor(max_workers=8) as executor:
            messages = list(executor.map(send, range(24)))
        self.assertEqual(len({m["id"] for m in messages}), 24)
        with self.box.connect() as db:
            rows = db.execute(
                "SELECT sender_session_id,session_id,state FROM messages"
            ).fetchall()
        self.assertEqual(len(rows), 24)
        self.assertEqual({r["state"] for r in rows}, {"queued"})
        self.assertEqual({r["session_id"] for r in rows}, {"session_fixture"})
        self.assertEqual(
            {r["sender_session_id"] for r in rows}, {f"codex_{i}" for i in range(24)}
        )

    def test_mcp_options_are_exposed_and_reach_the_dispatcher(self):
        for name in ("bridge_send", "bridge_dispatch"):
            spec = next(s for s in tool_specs("codex") if s["name"] == name)
            self.assertIn("priority", spec["inputSchema"]["properties"])
            self.assertIn("allow_busy_navigation", spec["inputSchema"]["properties"])
        seen = []

        def dispatch(mid, **kwargs):
            seen.append((mid, kwargs))
            return self.box.status(mid)

        message = invoke(
            self.box,
            "codex",
            "bridge_send",
            {
                "alias": "fixture",
                "body": "new priority",
                "priority": True,
                "allow_busy_navigation": True,
            },
            dispatch_handler=dispatch,
        )
        self.assertEqual(
            seen, [(message["id"], {"priority": True, "allow_busy_navigation": True})]
        )

    def test_child_defaults_and_explicit_normal_mode(self):
        for flags, wanted in [
            ([], True),
            (["--no-priority", "--no-allow-busy-navigation"], False),
        ]:
            with (
                self.subTest(flags=flags),
                patch.object(
                    sys, "argv", ["dispatch_background.py", "msg_" + "a" * 32, *flags]
                ),
                patch.object(dispatch_background, "Mailbox") as constructor,
                patch.object(
                    dispatch_queue, "enqueue", return_value={"dispatch_error": None}
                ) as enqueue,
                contextlib.redirect_stdout(io.StringIO()),
            ):
                self.assertEqual(dispatch_background.main(), 0)
                enqueue.assert_called_once_with(
                    constructor.return_value,
                    "msg_" + "a" * 32,
                    allow_busy_navigation=wanted,
                    priority=wanted,
                )
                constructor.return_value.dispatch.assert_not_called()

    def test_default_options_do_not_break_explicit_journal_recovery(self):
        with (
            patch.object(
                sys,
                "argv",
                [
                    "dispatch_background.py",
                    "msg_" + "a" * 32,
                    "--recover-unreceived",
                    "--expected-journal-sha256",
                    "b" * 64,
                ],
            ),
            patch.object(dispatch_background, "Mailbox"),
            patch.object(
                dispatch_background,
                "recover_unreceived",
                return_value={"dispatch_error": None},
            ) as recover,
            contextlib.redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(dispatch_background.main(), 0)
            recover.assert_called_once()

    def test_cli_reports_queue_acceptance_despite_previous_transport_error(self):
        for state, code in [("pending", 0), ("submitted", 0), ("held", 1)]:
            with (
                self.subTest(state=state),
                patch.object(
                    sys, "argv", ["dispatch_background.py", "msg_" + "a" * 32]
                ),
                patch.object(dispatch_background, "Mailbox"),
                patch.object(
                    dispatch_queue,
                    "enqueue",
                    return_value={
                        "dispatch_error": "previous Connection timed out",
                        "dispatch_queue": {"state": state},
                    },
                ),
                contextlib.redirect_stdout(io.StringIO()),
            ):
                self.assertEqual(dispatch_background.main(), code)

    def test_send_and_dispatch_default_to_priority_and_navigation(self):
        seen = []

        def handler(message_id, **options):
            seen.append(options)
            return self.box.status(message_id)

        message = invoke(
            self.box,
            "codex",
            "bridge_send",
            {"alias": "fixture", "body": "default priority"},
            dispatch_handler=handler,
        )
        invoke(
            self.box,
            "codex",
            "bridge_dispatch",
            {"message_id": message["id"]},
            dispatch_handler=handler,
        )
        self.assertEqual(seen, [{"allow_busy_navigation": True, "priority": True}] * 2)
        invoke(
            self.box,
            "codex",
            "bridge_dispatch",
            {
                "message_id": message["id"],
                "priority": False,
                "allow_busy_navigation": False,
            },
            dispatch_handler=handler,
        )
        self.assertEqual(seen[-1], {"allow_busy_navigation": False, "priority": False})

    def test_false_options_and_queue_only_do_not_dispatch(self):
        with patch.object(self.box, "dispatch") as dispatch:
            result = invoke(
                self.box,
                "codex",
                "bridge_send",
                {
                    "alias": "fixture",
                    "body": "fixture",
                    "dispatch": False,
                    "priority": False,
                },
            )
        self.assertEqual(result["state"], "queued")
        dispatch.assert_not_called()

    def test_invalid_modes_do_not_create_messages(self):
        for options in (
            {"priority": "false"},
            {"allow_busy_navigation": 1},
            {"dispatch": False, "priority": True},
        ):
            with self.subTest(options=options), self.assertRaises(ValueError):
                invoke(
                    self.box,
                    "codex",
                    "bridge_send",
                    {"alias": "fixture", "body": "fixture", **options},
                )
        with self.box.connect() as db:
            self.assertEqual(
                db.execute("SELECT count(*) FROM messages").fetchone()[0], 0
            )

    def test_options_only_add_fixed_child_flags(self):
        message = self.box.send("fixture", "fixture")
        response = SimpleNamespace(returncode=0, stdout=json.dumps(message), stderr="")
        with patch.object(
            mcp_dispatch.subprocess, "run", return_value=response
        ) as child:
            mcp_dispatch.dispatch_fresh(
                self.box, message["id"], priority=True, allow_busy_navigation=True
            )
        self.assertEqual(
            child.call_args.args[0][-2:], ["--allow-busy-navigation", "--priority"]
        )
        self.assertFalse(child.call_args.kwargs["shell"])

    def test_mailbox_guard_rechecks_ack_after_entering_transport(self):
        message = self.box.send("fixture", "fixture")

        def transport(binding, mid, **options):
            guard = options["dispatch_guard"]
            self.assertTrue(guard())
            self.box.receive("kimi", "session_fixture", mid)
            self.box.acknowledge("kimi", "session_fixture", mid)
            self.assertFalse(guard())
            return {"submitted": False, "skipped": "receiver_already_acknowledged"}

        with patch("desktop_delivery.deliver_wake", side_effect=transport):
            result = self.box.dispatch(message["id"])
        self.assertEqual(result["state"], "acknowledged")
        self.assertFalse(result["desktop_delivery"]["submitted"])

    def test_mailbox_guard_refuses_binding_change_while_waiting(self):
        message = self.box.send("fixture", "fixture")
        self.box.register(
            "kimi", "second_fixture", "Fixture2", "fixture", guard_ready=True
        )

        def transport(binding, mid, **options):
            self.box.bind("fixture", "kimi", "second_fixture", replace=True)
            options["dispatch_guard"]()
            self.fail("Changed binding was allowed")

        with patch("desktop_delivery.deliver_wake", side_effect=transport):
            result = self.box.dispatch(message["id"])
        self.assertIn("Binding changed while waiting", result["dispatch_error"])
        self.assertEqual(result["session_id"], "session_fixture")

    def test_priority_cannot_redispatch_an_acknowledged_message(self):
        message = self.box.send("fixture", "fixture")
        self.box.receive("kimi", "session_fixture", message["id"])
        self.box.acknowledge("kimi", "session_fixture", message["id"])
        with patch.object(mcp_dispatch.subprocess, "run") as child:
            result = mcp_dispatch.dispatch_fresh(self.box, message["id"], priority=True)
        self.assertEqual(result["state"], "acknowledged")
        child.assert_not_called()

    @unittest.skipUnless(os.name == "nt", "Windows file locking")
    def test_real_process_waits_then_acquires_same_lock(self):
        self._check_process_lock(release=True)

    @unittest.skipUnless(os.name == "nt", "Windows file locking")
    def test_real_process_timeout_does_not_enter_or_grow_lock(self):
        self._check_process_lock(release=False)

    def _check_process_lock(self, *, release):
        code = """import sys,time
from pathlib import Path
import desktop_delivery as d
d.ROOT=Path(sys.argv[1])
print('waiting',flush=True)
try:
 with d.desktop_lock(timeout=float(sys.argv[2])):
  print('acquired',flush=True)
except d.DriverError:
 print('timed_out',flush=True)
"""
        with patch.object(desktop_delivery, "ROOT", self.root):
            with desktop_delivery.desktop_lock():
                process = subprocess.Popen(
                    [
                        sys.executable,
                        "-c",
                        code,
                        str(self.root),
                        "3" if release else "0.25",
                    ],
                    cwd=ROOT,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
                self.assertEqual(process.stdout.readline().strip(), "waiting")
                time.sleep(0.4)
                if release:
                    self.assertIsNone(process.poll())
            out, err = process.communicate(timeout=6)
        self.assertEqual(process.returncode, 0, err)
        self.assertEqual(out.strip(), "acquired" if release else "timed_out")
        self.assertEqual((self.root / "data" / "desktop.lock").stat().st_size, 0)


class PriorityDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.rootpatch = patch.object(cdp, "ROOT", Path(self.temp.name))
        self.rootpatch.start()
        self.addCleanup(self.rootpatch.stop)
        self.lockpatch = patch("desktop_delivery.desktop_lock", contextlib.nullcontext)
        self.lockpatch.start()
        self.addCleanup(self.lockpatch.stop)
        self.client = FakeClient("kimi")
        self.original = self.client.evaluate
        self.ops = []

    def evaluate(self, op, binding, marker):
        self.ops.append(op)
        if op == "observe":
            return {**self.original(op, binding, marker), "generating": True}
        if op == "priority_queue_ready":
            return {"ready": True}
        if op == "priority_queue_submit":
            return {"priority_requested": True}
        return self.original(op, binding, marker)

    def send(self, harness="kimi"):
        with patch.object(self.client, "evaluate", side_effect=self.evaluate):
            return cdp.deliver_background(
                {**BINDING, "harness": harness},
                MID,
                lambda h: self.client,
                priority=True,
            )

    def test_kimi_targets_current_draft_with_ctrl_enter(self):
        result = self.send()
        keys = [
            args
            for method, args in self.client.commands
            if method == "Input.dispatchKeyEvent"
        ]
        self.assertEqual([k.get("modifiers") for k in keys], [2, 2])
        self.assertEqual(result["priority_action"], "kimi_steer_current_draft")
        self.assertFalse(result["receiver_verified"])

    def test_zcode_promotes_only_after_normal_submission(self):
        result = self.send("zcode")
        self.assertLess(
            self.ops.index("submit"), self.ops.index("priority_queue_submit")
        )
        self.assertEqual(result["priority_action"], "zcode_send_queued_now")

    def test_priority_duplicate_does_not_interrupt_twice(self):
        self.send()
        before = (len(self.client.commands), len(self.ops))
        result = self.send()
        self.assertTrue(result["duplicate_prevented"])
        self.assertEqual(before, (len(self.client.commands), len(self.ops)))

    def test_priority_loss_is_journaled_and_never_retried(self):
        def evaluate(op, binding, marker):
            if op == "priority_queue_submit":
                raise DriverError("response lost")
            return self.evaluate(op, binding, marker)

        with patch.object(self.client, "evaluate", side_effect=evaluate):
            with self.assertRaisesRegex(DriverError, "response lost"):
                cdp.deliver_background(
                    {**BINDING, "harness": "zcode"},
                    MID,
                    lambda h: self.client,
                    priority=True,
                )
        before = self.client.submits
        with self.assertRaisesRegex(DriverError, "uncertain"):
            self.send("zcode")
        self.assertEqual(self.client.submits, before)

    def test_ack_while_waiting_stops_before_any_client_is_created(self):
        factory = unittest.mock.Mock()
        result = cdp.deliver_background(
            BINDING, MID, factory, dispatch_guard=lambda: False
        )
        self.assertEqual(result["skipped"], "receiver_already_acknowledged")
        factory.assert_not_called()

    def test_ack_during_navigation_stops_before_typing(self):
        states = iter([True, False])
        result = cdp.deliver_background(
            BINDING, MID, lambda h: self.client, dispatch_guard=lambda: next(states)
        )
        self.assertEqual(result["skipped"], "receiver_already_acknowledged")
        self.assertEqual(self.client.commands, [])

    def test_priority_preserves_existing_draft(self):
        self.client.draft = True
        with self.assertRaisesRegex(DriverError, "existing_draft"):
            self.send()
        self.assertEqual(self.client.commands, [])

    def test_idle_priority_uses_normal_send(self):
        result = cdp.deliver_background(
            BINDING, MID, lambda h: self.client, priority=True
        )
        self.assertEqual(result["priority_action"], "idle_normal_send")
        self.assertTrue(
            all(
                "modifiers" not in args
                for method, args in self.client.commands
                if method == "Input.dispatchKeyEvent"
            )
        )


if __name__ == "__main__":
    unittest.main()
