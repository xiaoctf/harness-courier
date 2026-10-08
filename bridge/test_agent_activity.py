"""Activity uncertainty, passive Hook safety and recipient scoping regression."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_activity import (
    agent_status,
    inspect_target,
    record_event,
    wait_with_activity,
    with_activity,
)
from harness_courier.mcp_tools import invoke, tool_specs
from install_integrations import install
from receiver_hook import process

from bridge import BridgeError, Mailbox


class ActivityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.box = Mailbox(Path(self.temp.name) / "bridge.db")
        self.box.register("kimi", "kimi-A", "owned test", "test", True, True)
        self.box.bind("test", "kimi", "kimi-A")
        self.binding = self.box.binding("test")

    def query(self, native=None, **kwargs):
        return inspect_target(
            self.box,
            self.binding,
            probe=lambda _: native or {"available": False},
            **kwargs,
        )

    def test_peer_online_and_ack_are_not_running(self):
        mid = self.box.send("test", "owned communication test")["id"]
        self.box.receive("kimi", "kimi-A", mid)
        self.box.acknowledge("kimi", "kimi-A", mid)
        state = self.query()
        self.assertEqual(state["turn_state"], "unknown")
        self.assertEqual(state["message_counts"]["acknowledged"], 1)
        self.assertIn("progress_needs_verification", state["attention"])

    def test_stop_before_end_does_not_fake_idle_or_result(self):
        mid = self.box.send("test", "owned communication test")["id"]
        process(
            self.box,
            "kimi",
            {
                "session_id": "kimi-A",
                "hook_event_name": "Stop",
                "last_assistant_message": "PRIVATE",
                "tool_input": {"secret": "SECRET"},
            },
        )
        self.assertEqual(self.query()["turn_state"], "stop_observed")
        self.assertEqual(self.box.status(mid)["state"], "queued")
        state = self.query(
            {
                "available": True,
                "target_selected": True,
                "composer_verified": True,
                "generating": True,
            }
        )
        self.assertEqual(state["turn_state"], "running")
        self.assertNotIn("PRIVATE", json.dumps(state))
        self.assertNotIn("SECRET", json.dumps(state))

    def test_idle_with_ack_needs_attention(self):
        mid = self.box.send("test", "owned communication test")["id"]
        self.box.receive("kimi", "kimi-A", mid)
        self.box.acknowledge("kimi", "kimi-A", mid)
        state = self.query(
            {
                "available": True,
                "target_selected": True,
                "composer_verified": True,
                "generating": False,
            }
        )
        self.assertEqual(state["turn_state"], "idle")
        self.assertIn("idle_with_unfinished_messages", state["attention"])
        self.assertEqual(state["background_jobs"], "unknown")

    def test_stop_with_ack_requires_investigation_without_faking_idle(self):
        mid = self.box.send("test", "test")["id"]
        self.box.receive("kimi", "kimi-A", mid)
        self.box.acknowledge("kimi", "kimi-A", mid)
        record_event(self.box, "kimi", "kimi-A", "Stop")
        state = self.query()
        self.assertEqual(state["turn_state"], "stop_observed")
        self.assertIn("stop_observed_with_unfinished_messages", state["attention"])
        self.assertEqual(self.box.status(mid)["state"], "acknowledged")

    def test_submitted_unreceived_is_distinct_from_unsent_queue(self):
        from dispatch_queue import initialize

        initialize(self.box)
        sent = self.box.send("test", "sent")["id"]
        unsent = self.box.send("test", "unsent")["id"]
        with self.box.connect() as db:
            db.execute(
                "INSERT INTO dispatch_jobs VALUES(?, 'submitted',1,1,1,1,1,1,NULL,NULL)",
                (sent,),
            )
        result = self.query(
            {
                "available": True,
                "target_selected": True,
                "composer_verified": True,
                "generating": False,
            }
        )
        self.assertIn("idle_with_submitted_unreceived_messages", result["attention"])
        self.assertEqual(result["submitted_unreceived_messages"][0]["id"], sent)
        self.assertEqual(result["submitted_unreceived_count"], 1)
        self.assertEqual(self.box.status(unsent)["state"], "queued")

    def test_wait_surfaces_stop_gap_without_faking_result_or_resending(self):
        mid = self.box.send("test", "test")["id"]
        self.box.receive("kimi", "kimi-A", mid)
        self.box.acknowledge("kimi", "kimi-A", mid)
        with self.box.connect() as db:
            db.execute("UPDATE messages SET acknowledged_at=1 WHERE id=?", (mid,))
        record_event(self.box, "kimi", "kimi-A", "Stop")
        with patch(
            "agent_activity.probe_target",
            side_effect=AssertionError("short wait must not probe"),
        ):
            result = wait_with_activity(self.box, mid, timeout=0)
        self.assertEqual(result["wait_reason"], "attention_required")
        self.assertEqual(result["state"], "acknowledged")
        self.assertIsNone(result["result"])
        self.assertEqual(result["agent"]["turn_state"], "stop_observed")

    def test_wait_uses_frozen_target_and_prefers_real_terminal_receipt(self):
        mid = self.box.send("test", "test")["id"]
        self.box.receive("kimi", "kimi-A", mid)
        self.box.reply("kimi", "kimi-A", mid, "OK")
        result = wait_with_activity(self.box, mid, timeout=0)
        self.assertEqual((result["wait_reason"], result["result"]), ("receipt", "OK"))
        for bad in (True, float("nan"), 56):
            with self.assertRaises(BridgeError):
                wait_with_activity(self.box, mid, timeout=bad)

    def test_wait_terminal_receipt_can_still_report_agent_running(self):
        mid = self.box.send("test", "test")["id"]
        self.box.receive("kimi", "kimi-A", mid)
        self.box.reply("kimi", "kimi-A", mid, "OK")
        with patch(
            "agent_activity.probe_target",
            return_value={
                "available": True,
                "target_selected": True,
                "composer_verified": True,
                "generating": True,
            },
        ):
            result = wait_with_activity(self.box, mid, timeout=30)
        self.assertEqual(result["state"], "completed")
        self.assertEqual(result["agent"]["turn_state"], "running")
        self.assertEqual(result["wait_reason"], "receipt")

    def test_other_selected_chat_never_qualifies(self):
        state = self.query(
            {
                "available": True,
                "target_selected": False,
                "composer_verified": True,
                "generating": False,
            }
        )
        self.assertEqual(state["turn_state"], "unknown")

    def test_verified_exact_kimi_sidebar_observes_without_switching(self):
        for turn in ("running", "idle"):
            state = self.query(
                {
                    "available": True,
                    "target_selected": False,
                    "sidebar": {"verified": True, "turn_state": turn},
                }
            )
            self.assertEqual(state["turn_state"], turn)
            self.assertEqual(state["turn_evidence"], "native_exact_session_sidebar")
        state = self.query(
            {
                "available": True,
                "target_selected": False,
                "sidebar": {"verified": False, "turn_state": "idle"},
            }
        )
        self.assertEqual(state["turn_state"], "unknown")

    def zcode_native(self, turn="idle", **overrides):
        self.box.register("zcode", "zcode-A", "owned test", "E:/fixture", True, True)
        self.box.bind("ztest", "zcode", "zcode-A")
        self.binding = self.box.binding("ztest")
        return {
            "available": True,
            "target_selected": False,
            "session_activity": {
                "verified": True,
                "session_id": "zcode-A",
                "workspace_path": "E:/fixture",
                "source_availability": "online",
                "turn_state": turn,
                "phase": "completedSuccess" if turn == "idle" else "running",
                **overrides,
            },
        }

    def test_unselected_zcode_controller_outlives_old_stop(self):
        native = self.zcode_native()
        record_event(self.box, "zcode", "zcode-A", "Stop")
        for turn in ("idle", "running", "approval_requested", "waiting_for_input"):
            native["session_activity"]["turn_state"] = turn
            state = self.query(native, clock=lambda: 10**11)
            self.assertEqual(state["turn_state"], turn)
            self.assertEqual(state["turn_evidence"], "native_exact_session_controller")
            self.assertGreater(state["last_event_age_seconds"], 300)

    def test_zcode_exact_controller_identity_required(self):
        for changed in (
            {"verified": False},
            {"session_id": "other"},
            {"workspace_path": "E:/other"},
            {"source_availability": "offline"},
            {"turn_state": "completed"},
        ):
            with self.subTest(changed=changed):
                self.assertEqual(
                    self.query(self.zcode_native(**changed))["turn_state"], "unknown"
                )
        native = self.zcode_native()
        self.binding = self.box.binding("test")
        self.assertEqual(self.query(native)["turn_state"], "unknown")

    def test_probe_gets_frozen_recipient_workspace(self):
        native = self.zcode_native()
        message = self.box.send("ztest", "test")
        self.box.register("zcode", "zcode-B", "second", "E:/other", True, True)
        self.box.bind("ztest", "zcode", "zcode-B", replace=True)
        with patch("agent_activity.probe_target", return_value=native) as probe:
            result = with_activity(self.box, message)
        self.assertEqual(probe.call_args.args[0]["workspace_path"], "E:/fixture")
        self.assertEqual(result["agent"]["session_id"], "zcode-A")

    def test_zcode_native_interaction_replaces_old_permission(self):
        native = self.zcode_native("running")
        record_event(self.box, "zcode", "zcode-A", "PermissionRequest")
        state = self.query(native)
        self.assertEqual(state["turn_state"], "running")
        self.assertEqual(state["turn_evidence"], "native_exact_session_controller")
        self.assertNotIn("approval_requested", state["attention"])

    def test_zcode_controls_and_controller_disagreement_stays_unknown(self):
        for turn, generating in (("idle", True), ("running", False)):
            native = self.zcode_native(turn)
            native.update(
                target_selected=True, composer_verified=True, generating=generating
            )
            record_event(self.box, "zcode", "zcode-A", "Stop")
            state = self.query(native)
            self.assertEqual(state["turn_state"], "unknown")
            self.assertIn("native_state_conflict", state["attention"])
        native = self.zcode_native()
        native.update(target_selected=True, composer_verified=True, generating=False)
        native["session_activity"] = {
            "verified": False,
            "reason": "target_source_offline",
        }
        self.assertEqual(self.query(native)["turn_state"], "unknown")

    def test_zcode_error_and_interaction_request_attention(self):
        state = self.query(self.zcode_native(phase="error"))
        self.assertEqual(state["turn_state"], "idle")
        self.assertIn("native_turn_error", state["attention"])
        state = self.query(self.zcode_native("waiting_for_input"))
        self.assertIn("waiting_for_input", state["attention"])

    def test_wait_returns_native_user_input_attention_without_resending(self):
        native = self.zcode_native("waiting_for_input")
        message = self.box.send("ztest", "test")
        self.box.receive("zcode", "zcode-A", message["id"])
        self.box.acknowledge("zcode", "zcode-A", message["id"])
        with patch("agent_activity.probe_target", return_value=native):
            result = wait_with_activity(self.box, message["id"], timeout=30)
        self.assertEqual(result["wait_reason"], "attention_required")
        self.assertEqual(result["state"], "acknowledged")
        self.assertEqual(result["agent"]["turn_state"], "waiting_for_input")
        self.assertEqual(result["agent"]["message_counts"], {"acknowledged": 1})

    def test_ambiguous_composer_never_qualifies(self):
        state = self.query(
            {
                "available": True,
                "target_selected": True,
                "composer_verified": False,
                "generating": False,
            }
        )
        self.assertEqual(state["turn_state"], "unknown")

    def test_passive_event_does_not_deliver_or_change_peer(self):
        before = self.box.peers()["peers"]
        mid = self.box.send("test", "test")["id"]
        for event in ("Stop", "PostToolUse", "PostToolUseFailure", "PermissionRequest"):
            output, code = process(
                self.box, "kimi", {"session_id": "kimi-A", "hook_event_name": event}
            )
            self.assertEqual((output, code), ({}, 0))
        self.assertEqual(self.box.peers()["peers"], before)
        self.assertEqual(self.box.status(mid)["state"], "queued")
        self.assertEqual(self.query()["turn_state"], "approval_requested")

    def test_progress_is_observed_but_not_current_running_proof(self):
        record_event(self.box, "kimi", "kimi-A", "PostToolUse")
        state = self.query()
        self.assertEqual(state["turn_state"], "unknown")
        self.assertLess(state["last_progress_age_seconds"], 2)
        self.assertEqual(state["last_hook"]["revision"], 1)

    def test_old_permission_event_is_not_current_wait_proof(self):
        record_event(self.box, "kimi", "kimi-A", "PermissionRequest")
        state = self.query(clock=lambda: 10**11)
        self.assertEqual(state["turn_state"], "unknown")

    def test_old_stop_event_is_historical_not_current_stop_proof(self):
        record_event(self.box, "kimi", "kimi-A", "Stop")
        state = self.query(clock=lambda: 10**11)
        self.assertEqual(state["turn_state"], "unknown")
        self.assertEqual(state["last_hook"]["last_event"], "Stop")

    def test_frozen_message_recipient_survives_rebind(self):
        message = self.box.send("test", "test")
        self.box.register("kimi", "kimi-B", "second", "test", True, True)
        self.box.bind("test", "kimi", "kimi-B", replace=True)
        with patch("agent_activity.probe_target", return_value={"available": False}):
            state = with_activity(self.box, message)
        self.assertEqual(state["agent"]["session_id"], "kimi-A")
        self.assertEqual(state["agent"]["binding_revision"], 1)

    def test_query_rejects_concurrent_rebind(self):
        self.box.register("kimi", "kimi-B", "second", "test", True, True)

        def probe(_):
            self.box.bind("test", "kimi", "kimi-B", replace=True)
            return {"available": False}

        with (
            patch("agent_activity.probe_target", side_effect=probe),
            self.assertRaises(BridgeError),
        ):
            agent_status(self.box, "test")

    def test_tool_readonly_and_harness_scope_and_validation(self):
        spec = next(
            t for t in tool_specs("codex") if t["name"] == "courier_get_agent_status"
        )
        self.assertTrue(spec["annotations"]["readOnlyHint"])
        self.assertNotIn(
            "courier_get_agent_status", [t["name"] for t in tool_specs("kimi")]
        )
        for args in (
            {"alias": "test", "observe_native": 1},
            {"alias": "test", "stale_after_seconds": float("nan")},
            {"alias": "test", "stale_after_seconds": True},
        ):
            with self.assertRaises(BridgeError):
                invoke(self.box, "codex", "bridge_agent_status", args)
        with patch(
            "agent_activity.probe_target", side_effect=AssertionError("must not probe")
        ):
            result = invoke(
                self.box,
                "codex",
                "bridge_agent_status",
                {"alias": "test", "observe_native": False},
            )
            self.assertEqual(result["turn_state"], "unknown")

    def test_installer_upgrades_existing_hooks_preserves_unrelated_idempotent(self):
        root = Path(self.temp.name) / "home"
        install(root, Path("python"), apply=True)
        # Simulate the previous install by removing only newly owned passive events.
        import tomllib

        path = root / ".kimi-code" / "config.toml"
        raw = path.read_text()
        from agent_activity import PASSIVE_EVENTS

        for event in PASSIVE_EVENTS:
            start = raw.index("[[hooks]]\nevent = " + json.dumps(event))
            end = raw.find("[[hooks]]", start + 1)
            if end < 0:
                end = raw.index("# END HARNESS BRIDGE", start)
            raw = raw[:start] + raw[end:]
        path.write_text(raw)
        self.assertEqual(len(tomllib.loads(raw)["hooks"]), 3)
        install(root, Path("python"), apply=True)
        parsed = tomllib.loads(path.read_text())
        self.assertEqual(len(parsed["hooks"]), 7)
        second = install(root, Path("python"), apply=True)
        self.assertTrue(all(not item["changed"] for item in second["configuration"]))


if __name__ == "__main__":
    unittest.main()
