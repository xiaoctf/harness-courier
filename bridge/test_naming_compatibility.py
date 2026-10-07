"""Public naming migration regressions; all mailbox/config writes are temporary."""

from __future__ import annotations

import importlib
import json
import os
import subprocess
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest.mock import patch

import install_integrations as installer
from harness_courier import BridgeError, Mailbox
from harness_courier.app_registry import load_apps
from harness_courier.mcp_tools import invoke, tool_specs
from harness_courier.tool_names import LEGACY_TO_CANONICAL

ROOT = Path(__file__).resolve().parent


class NamingCompatibilityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.box = Mailbox(self.root / "mail.db")
        self.box.register(
            "kimi", "fixture-kimi", "Fixture", "fixture", guard_ready=True
        )
        self.box.register(
            "zcode", "fixture-zcode", "Fixture", "fixture", guard_ready=True
        )

    def test_module_aliases_share_globals_and_classes(self):
        pairs = {
            "harness_bridge.errors": "harness_courier.errors",
            "harness_bridge.paths": "harness_courier.paths",
            "harness_bridge.mailbox": "harness_courier.mailbox",
            "harness_bridge.tools": "harness_courier.mcp_tools",
            "harness_bridge.mcp": "harness_courier.mcp_server",
            "harness_bridge.apps": "harness_courier.app_registry",
            "harness_bridge.cli": "harness_courier.cli",
            "harness_bridge.launcher": "harness_courier.launcher",
            "cdp_delivery": "cdp_transport",
            "mcp_dispatch": "dispatch_runner",
            "setup_bridge": "install_integrations",
        }
        for old, new in pairs.items():
            with self.subTest(module=old):
                self.assertIs(
                    importlib.import_module(old), importlib.import_module(new)
                )
        self.assertIs(importlib.import_module("harness_bridge").Mailbox, Mailbox)
        self.assertIs(importlib.import_module("bridge").BridgeError, BridgeError)

    def test_tool_aliases_preserve_schemas_annotations_and_role_scope(self):
        for role in ("codex", "kimi", "zcode"):
            specs = tool_specs(role)
            names = {tool["name"]: tool for tool in specs}
            self.assertEqual(len(names), 12)
            self.assertTrue(all(t["name"].startswith("courier_") for t in specs[:6]))
            for old, new in LEGACY_TO_CANONICAL.items():
                with self.subTest(role=role, operation=old):
                    self.assertEqual(old in names, new in names)
                    if old in names:
                        self.assertEqual(
                            names[old]["inputSchema"], names[new]["inputSchema"]
                        )
                        self.assertEqual(
                            names[old]["annotations"], names[new]["annotations"]
                        )
                        self.assertIn("Legacy alias", names[old]["description"])
                    else:
                        for name in (old, new):
                            with self.assertRaisesRegex(BridgeError, "unavailable"):
                                invoke(self.box, role, name, {})

    def test_mixed_names_complete_same_message_and_preserve_terminal_guard(self):
        for role in ("kimi", "zcode"):
            for canonical_sender in (True, False):
                with self.subTest(role=role, canonical_sender=canonical_sender):

                    def sender(old):
                        return LEGACY_TO_CANONICAL[old] if canonical_sender else old

                    def receiver(old):
                        return old if canonical_sender else LEGACY_TO_CANONICAL[old]

                    alias = f"fixture-{role}-{canonical_sender}"
                    invoke(
                        self.box,
                        "codex",
                        sender("bridge_bind"),
                        {
                            "alias": alias,
                            "harness": role,
                            "session_id": f"fixture-{role}",
                        },
                    )
                    sent = invoke(
                        self.box,
                        "codex",
                        sender("bridge_send"),
                        {
                            "alias": alias,
                            "body": "test task",
                            "dispatch": False,
                        },
                    )
                    args = {
                        "caller_session_id": f"fixture-{role}",
                        "message_id": sent["id"],
                    }
                    self.assertEqual(
                        invoke(self.box, role, receiver("bridge_inbox"), args)[0][
                            "state"
                        ],
                        "delivered",
                    )
                    self.assertEqual(
                        invoke(self.box, role, receiver("bridge_ack"), args)["state"],
                        "acknowledged",
                    )
                    invoke(
                        self.box,
                        role,
                        receiver("bridge_reply"),
                        {**args, "body": "verified result"},
                    )
                    self.assertEqual(
                        invoke(
                            self.box,
                            "codex",
                            sender("bridge_status"),
                            {"message_id": sent["id"]},
                        )["result"],
                        "verified result",
                    )
                    self.assertEqual(
                        invoke(
                            self.box,
                            "codex",
                            sender("bridge_wait"),
                            {"message_id": sent["id"], "timeout": 0},
                        )["state"],
                        "completed",
                    )
                    with patch("dispatch_runner.subprocess.run") as child:
                        from dispatch_runner import dispatch_fresh

                        final = invoke(
                            self.box,
                            "codex",
                            sender("bridge_dispatch"),
                            {"message_id": sent["id"]},
                            dispatch_handler=lambda message_id: dispatch_fresh(
                                self.box, message_id
                            ),
                        )
                    child.assert_not_called()
                    self.assertEqual(final["state"], "completed")
                    self.assertIn(
                        alias,
                        {
                            b["alias"]
                            for b in invoke(
                                self.box, "codex", sender("bridge_peers"), {}
                            )["bindings"]
                        },
                    )

    def test_environment_prefers_courier_and_falls_back_to_bridge(self):
        old = self.root / "old.toml"
        new = self.root / "new.toml"
        old.write_text("[apps.kimi]\nport=51001\n", encoding="utf-8")
        new.write_text("[apps.kimi]\nport=51003\n", encoding="utf-8")
        with patch.dict(os.environ, {"HARNESS_BRIDGE_APPS": str(old)}, clear=True):
            self.assertEqual(load_apps()["kimi"][1], 51001)
            with patch.dict(os.environ, {"HARNESS_COURIER_APPS": str(new)}):
                self.assertEqual(load_apps()["kimi"][1], 51003)
                new.unlink()
                with self.assertRaisesRegex(RuntimeError, "Cannot read"):
                    load_apps()

    def test_original_database_and_wake_protocol_are_stable(self):
        from harness_courier.paths import DEFAULT_DB
        from receiver_hook import WAKE

        self.assertEqual(DEFAULT_DB, ROOT / "data" / "bridge.sqlite3")
        self.assertIsNotNone(
            WAKE.fullmatch("[HARNESS_BRIDGE_WAKE:msg_" + "a" * 32 + "]")
        )
        self.assertEqual(
            (ROOT / "harness_courier/composer_guard.js").read_bytes(),
            (ROOT / "harness_bridge/composer.js").read_bytes(),
        )

    def test_fresh_installer_uses_canonical_name_and_is_idempotent(self):
        first = installer.install(self.root, Path(sys.executable), apply=True)
        self.assertTrue(first["applied"])
        second = installer.install(self.root, Path(sys.executable), apply=True)
        self.assertTrue(all(not row["changed"] for row in second["configuration"]))
        servers = tomllib.loads((self.root / ".codex/config.toml").read_text())[
            "mcp_servers"
        ]
        self.assertEqual(set(servers), {"harness_courier"})

    def test_installer_retains_legacy_registrations_and_hook_blocks(self):
        installer.install(self.root, Path(sys.executable), apply=True)
        for path, _, _, _ in installer.build_changes(self.root, Path(sys.executable)):
            path.write_text(
                path.read_text(encoding="utf-8").replace(
                    "harness_courier", "harness_bridge"
                ),
                encoding="utf-8",
            )
        before = {
            p: p.read_bytes()
            for p, _, _, _ in installer.build_changes(self.root, Path(sys.executable))
        }
        result = installer.install(self.root, Path(sys.executable), apply=True)
        self.assertTrue(all(not row["changed"] for row in result["configuration"]))
        self.assertTrue(all(p.read_bytes() == value for p, value in before.items()))

    def test_conflicts_under_either_name_do_not_partially_write(self):
        for name in ("harness_bridge", "harness_courier"):
            for relative in (
                ".codex/config.toml",
                ".kimi-code/mcp.json",
                ".zcode/cli/config.json",
            ):
                with (
                    self.subTest(name=name, path=relative),
                    tempfile.TemporaryDirectory() as tmp,
                ):
                    home = Path(tmp)
                    path = home / relative
                    path.parent.mkdir(parents=True)
                    if relative.endswith(".toml"):
                        text = f'[mcp_servers.{name}]\ncommand="foreign"\nargs=[]\n'
                    else:
                        servers = {name: {"command": "foreign", "args": []}}
                        text = json.dumps(
                            {"mcpServers": servers}
                            if "kimi" in relative
                            else {"mcp": {"servers": servers}}
                        )
                    path.write_text(text, encoding="utf-8")
                    with self.assertRaisesRegex(ValueError, "conflict"):
                        installer.install(home, Path(sys.executable), apply=True)
                    self.assertEqual(path.read_text(encoding="utf-8"), text)
                    self.assertEqual(
                        {
                            p.relative_to(home).as_posix()
                            for p in home.rglob("*")
                            if p.is_file()
                        },
                        {relative},
                    )

    def test_duplicate_registration_is_rejected(self):
        expected = installer.server(Path(sys.executable), "codex")
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            installer.registered_name(
                {name: expected for name in (installer.NAME, installer.LEGACY_NAME)},
                expected,
                "Codex",
            )

    def test_canonical_and_legacy_mcp_entries_accept_both_tool_names(self):
        requests = [
            {"id": 1, "method": "initialize", "params": {}},
            {"id": 2, "method": "tools/list"},
            *[
                {
                    "id": i,
                    "method": "tools/call",
                    "params": {"name": name, "arguments": {}},
                }
                for i, name in enumerate(("courier_list_sessions", "bridge_peers"), 3)
            ],
        ]
        for module in ("harness_courier", "harness_bridge"):
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    module,
                    "--db",
                    str(self.box.path),
                    "mcp",
                    "--harness",
                    "codex",
                ],
                cwd=ROOT,
                input="".join(json.dumps(r) + "\n" for r in requests),
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=10,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            responses = [json.loads(line) for line in result.stdout.splitlines()]
            self.assertEqual(
                responses[0]["result"]["serverInfo"]["name"], "harness-courier"
            )
            self.assertEqual(len(responses[1]["result"]["tools"]), 12)
            self.assertEqual(responses[2]["result"], responses[3]["result"])
            self.assertFalse(responses[2]["result"]["isError"])

    @unittest.skipUnless(os.name == "nt", "Windows CMD entry points")
    def test_cmd_environment_prefers_canonical_interpreter(self):
        env = dict(
            os.environ,
            HARNESS_COURIER_PYTHON=sys.executable,
            HARNESS_BRIDGE_PYTHON=str(self.root / "missing.exe"),
        )
        for folder in ("bridge", "launcher"):
            result = subprocess.run(
                [
                    "cmd",
                    "/d",
                    "/c",
                    str(ROOT.parent / folder / "start_background.cmd"),
                    "--help",
                ],
                env=env,
                cwd=self.root,
                capture_output=True,
                text=True,
                timeout=10,
            )
            self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
