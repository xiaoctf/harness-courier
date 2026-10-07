"""Scoped MCP dispatch tests: no model shell, real desktop or user configuration."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import mcp_dispatch
from harness_bridge.mcp import call_tool
from harness_bridge.tools import invoke

from bridge import Mailbox, tool_specs

ROOT = Path(__file__).resolve().parent


class McpDispatchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.box = Mailbox(self.root / "fixture.db")
        self.box.register(
            "kimi", "permissions-fixture", "Fixture", "fixture", guard_ready=True
        )
        self.box.bind("fixture-peer", "kimi", "permissions-fixture")
        self.message = self.box.send(
            "fixture-peer", "temporary test text", dispatch=False
        )

    def response(self, **changes):
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps(
                {**self.message, "desktop_delivery": {"submitted": True}, **changes}
            ),
            stderr="",
        )

    def test_annotations_distinguish_queries_from_side_effects(self):
        annotations = {
            tool["name"]: tool["annotations"]
            for role in ("codex", "kimi", "zcode")
            for tool in tool_specs(role)
        }
        self.assertEqual(
            {name for name, hints in annotations.items() if hints["readOnlyHint"]},
            {
                "bridge_peers",
                "bridge_status",
                "bridge_wait",
                "courier_list_sessions",
                "courier_get_message_status",
                "courier_wait_for_receipt",
            },
        )
        self.assertTrue(annotations["bridge_send"]["openWorldHint"])
        self.assertFalse(annotations["bridge_send"]["idempotentHint"])
        self.assertTrue(annotations["bridge_bind"]["destructiveHint"])
        self.assertFalse(annotations["bridge_inbox"]["readOnlyHint"])
        self.assertFalse(annotations["bridge_inbox"]["idempotentHint"])
        self.assertFalse(annotations["bridge_bind"]["idempotentHint"])

    def test_fixed_child_uses_server_database_and_no_shell(self):
        with patch.object(
            mcp_dispatch.subprocess, "run", return_value=self.response()
        ) as child:
            result = mcp_dispatch.dispatch_fresh(self.box, self.message["id"])
        command = child.call_args.args[0]
        self.assertEqual(
            command,
            [
                sys.executable,
                str(ROOT / "dispatch_background.py"),
                "--db",
                str(self.box.path.resolve()),
                self.message["id"],
                "--allow-busy-navigation",
                "--priority",
            ],
        )
        self.assertIs(child.call_args.kwargs["shell"], False)
        self.assertEqual(child.call_args.kwargs["timeout"], 45)
        self.assertTrue(result["desktop_delivery"]["submitted"])
        self.assertEqual(self.box.status(self.message["id"])["state"], "queued")

    def test_mcp_send_calls_dispatcher_and_preserves_args(self):
        arguments = {"alias": "fixture-peer", "body": "new fixture message"}
        with patch(
            "harness_bridge.mcp.dispatch_fresh",
            side_effect=lambda box, mid, **options: box.status(mid),
        ) as dispatch:
            result = call_tool(
                self.box, "codex", {"name": "bridge_send", "arguments": arguments}
            )
        self.assertFalse(result["isError"])
        self.assertEqual(dispatch.call_count, 1)
        returned = json.loads(result["content"][0]["text"])
        self.assertEqual(dispatch.call_args.args[1], returned["id"])
        self.assertEqual(
            arguments, {"alias": "fixture-peer", "body": "new fixture message"}
        )

    def test_queue_only_send_does_not_spawn_dispatcher(self):
        with patch("harness_bridge.mcp.dispatch_fresh") as dispatch:
            result = call_tool(
                self.box,
                "codex",
                {
                    "name": "bridge_send",
                    "arguments": {
                        "alias": "fixture-peer",
                        "body": "queued fixture",
                        "dispatch": False,
                    },
                },
            )
        self.assertFalse(result["isError"])
        dispatch.assert_not_called()

    def test_explicit_dispatch_reuses_the_original_id(self):
        with patch(
            "harness_bridge.mcp.dispatch_fresh", return_value=self.message
        ) as dispatch:
            result = call_tool(
                self.box,
                "codex",
                {
                    "name": "bridge_dispatch",
                    "arguments": {"message_id": self.message["id"]},
                },
            )
        self.assertFalse(result["isError"])
        dispatch.assert_called_once_with(
            self.box,
            message_id=self.message["id"],
            allow_busy_navigation=True,
            priority=True,
        )
        self.assertEqual(len(self.box.receive("kimi", "permissions-fixture")), 1)

    def test_ack_and_terminal_messages_never_spawn(self):
        self.box.receive("kimi", "permissions-fixture", self.message["id"])
        self.box.acknowledge("kimi", "permissions-fixture", self.message["id"])
        with patch.object(mcp_dispatch.subprocess, "run") as child:
            self.assertEqual(
                mcp_dispatch.dispatch_fresh(self.box, self.message["id"])["state"],
                "acknowledged",
            )
            self.box.reply("kimi", "permissions-fixture", self.message["id"], "done")
            self.assertEqual(
                mcp_dispatch.dispatch_fresh(self.box, self.message["id"])["state"],
                "completed",
            )
        child.assert_not_called()

    def test_timeout_keeps_original_message_and_uncertainty(self):
        with patch.object(
            mcp_dispatch.subprocess,
            "run",
            side_effect=subprocess.TimeoutExpired("fixture", 45),
        ) as child:
            result = mcp_dispatch.dispatch_fresh(self.box, self.message["id"])
        self.assertEqual(child.call_count, 1)
        self.assertEqual(result["id"], self.message["id"])
        self.assertEqual(result["state"], "queued")
        self.assertIsNone(result["acknowledged_at"])
        self.assertTrue(result["desktop_delivery"]["outcome_uncertain"])
        self.assertIn("timed out", result["dispatch_error"])
        self.assertIsNone(result["desktop_delivery"]["submitted"])

    def test_bad_child_responses_never_promote_receipts_or_leak_stderr(self):
        for output in (
            "not JSON",
            "[]",
            json.dumps({**self.message, "session_id": "wrong-session"}),
        ):
            with (
                self.subTest(output=output),
                patch.object(
                    mcp_dispatch.subprocess,
                    "run",
                    return_value=SimpleNamespace(
                        returncode=0, stdout=output, stderr="PRIVATE_LOG_SENTINEL"
                    ),
                ),
            ):
                result = mcp_dispatch.dispatch_fresh(self.box, self.message["id"])
            self.assertTrue(result["desktop_delivery"]["outcome_uncertain"])
            self.assertEqual(result["state"], "queued")
            self.assertNotIn("PRIVATE_LOG_SENTINEL", json.dumps(result))

    def test_child_reported_failure_keeps_original_id(self):
        response = SimpleNamespace(
            returncode=1, stdout=json.dumps({"error": "binding changed"}), stderr=""
        )
        with patch.object(mcp_dispatch.subprocess, "run", return_value=response):
            result = mcp_dispatch.dispatch_fresh(self.box, self.message["id"])
        self.assertEqual(result["id"], self.message["id"])
        self.assertEqual(result["dispatch_error"], "binding changed")

    def test_unsupported_arguments_cannot_become_child_commands(self):
        with patch.object(mcp_dispatch.subprocess, "run") as child:
            with self.assertRaises(ValueError):
                mcp_dispatch.dispatch_fresh(self.box, "msg_bad & arbitrary-command")
            with self.assertRaises(ValueError):
                invoke(
                    self.box,
                    "codex",
                    "bridge_dispatch",
                    {"message_id": self.message["id"], "command": "arbitrary-command"},
                )
        child.assert_not_called()

    def test_real_stdio_send_dispatches_internally_against_a_temporary_mailbox(self):
        # Neither fake executable exists, so endpoint ownership prevents all CDP
        # connections even if another program happens to own the chosen port.
        config = self.root / "apps.toml"
        config.write_text(
            f'[apps.kimi]\nexecutable="{(self.root / "not-installed-kimi.exe").as_posix()}"\nport=65534\n[apps.zcode]\nexecutable="{(self.root / "not-installed-zcode.exe").as_posix()}"\nport=65533\n',
            encoding="utf-8",
        )
        request = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "bridge_send",
                "arguments": {
                    "alias": "fixture-peer",
                    "body": "stdio dispatch fixture",
                },
            },
        }
        result = subprocess.run(
            [
                sys.executable,
                str(ROOT / "bridge.py"),
                "--db",
                str(self.box.path),
                "mcp",
                "--harness",
                "codex",
            ],
            input=json.dumps(request) + "\n",
            env=dict(os.environ, HARNESS_BRIDGE_APPS=str(config)),
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=15,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        response = json.loads(result.stdout)["result"]
        self.assertFalse(response["isError"])
        sent = json.loads(response["content"][0]["text"])
        self.assertNotEqual(sent["id"], self.message["id"])
        self.assertEqual(sent["state"], "queued")
        self.assertIn(
            sent["dispatch_queue"]["state"], {"pending", "working", "held", "submitted"}
        )
        self.assertIsNone(sent["desktop_delivery"]["submitted"])
        # Terminate only the worker spawned for this temporary DB, not any app.
        import psutil

        pid = sent["dispatch_queue"]["worker_pid"]
        if pid:
            try:
                worker = psutil.Process(pid)
                command = worker.cmdline()
                self.assertIn(str(self.box.path.resolve()), command)
                self.assertTrue(
                    any(Path(arg).name == "dispatch_worker.py" for arg in command)
                )
                worker.terminate()
                worker.wait(timeout=5)
            except psutil.NoSuchProcess:
                pass
        self.assertEqual(self.box.status(sent["id"])["body"], "stdio dispatch fixture")


if __name__ == "__main__":
    unittest.main()
