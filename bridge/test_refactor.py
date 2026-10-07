"""Regressions for configurable paths, shared launcher and extracted adapters.

All writes use temporary directories. Desktop processes and listeners are fakes;
only compatibility CLI subprocesses are real. No user chat or config is touched.
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from harness_bridge import BridgeError, Mailbox, apps, launcher
from harness_bridge.errors import DriverError
from harness_bridge.mcp import serve
from harness_bridge.tools import invoke

ROOT = Path(__file__).resolve().parent


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config = self.root / "apps.toml"

    def test_partial_override_preserves_other_app(self):
        executable = (self.root / "kimi.exe").as_posix()
        self.config.write_text(
            f'[apps.kimi]\nexecutable="{executable}"\nport=51001\n', encoding="utf-8"
        )
        configured = apps.load_apps(self.config)
        self.assertEqual(configured["kimi"], (Path(executable), 51001))
        self.assertEqual(configured["zcode"], apps.DEFAULT_APPS["zcode"])
        self.assertEqual(apps.DEFAULT_APPS["kimi"][1], 49371)

    def test_explicit_missing_config_never_falls_back(self):
        with patch.dict(os.environ, {"HARNESS_BRIDGE_APPS": str(self.config)}):
            with self.assertRaisesRegex(DriverError, "Cannot read"):
                apps.load_apps()

    def test_environment_override_is_shared(self):
        self.config.write_text("[apps.kimi]\nport=51001\n", encoding="utf-8")
        with patch.dict(os.environ, {"HARNESS_BRIDGE_APPS": str(self.config)}):
            self.assertEqual(apps.load_apps()["kimi"][1], 51001)

    def test_invalid_overrides_fail_closed(self):
        cases = [
            ("[apps.other]\nport=51001\n", "Unknown application"),
            ('[apps.kimi]\nexecutable="relative.exe"\n', "absolute path"),
            ("[apps.kimi]\nport=true\n", "integer"),
            ("[apps.kimi]\nport=80\n", "integer"),
            ("[apps.kimi]\nport=49372\n", "distinct"),
            ("[apps.kimi]\nforeground=true\n", "Unknown application"),
        ]
        for text, message in cases:
            with self.subTest(text=text):
                self.config.write_text(text, encoding="utf-8")
                with self.assertRaisesRegex(DriverError, message):
                    apps.load_apps(self.config)

    def test_empty_and_malformed_config_is_rejected(self):
        for text in ("", "[apps.kimi", "[unexpected]\nvalue=1\n"):
            with self.subTest(text=text):
                self.config.write_text(text, encoding="utf-8")
                with self.assertRaises(DriverError):
                    apps.load_apps(self.config)


class LauncherTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        registry = {}
        for offset, name in enumerate(("kimi", "zcode")):
            exe = self.root / (name + ".exe")
            exe.touch()
            registry[name] = (exe, 51001 + offset)
        self.registry = registry
        registry_patch = patch.object(launcher, "APPS", registry)
        registry_patch.start()
        self.addCleanup(registry_patch.stop)

    def test_check_never_starts_or_closes_apps(self):
        with (
            patch.object(launcher, "endpoint_ready", return_value=False),
            patch.object(launcher, "running_app", return_value=True),
            patch.object(launcher.subprocess, "Popen") as spawn,
            contextlib.redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(launcher.launch_apps(list(self.registry), check=True), 0)
        spawn.assert_not_called()

    def test_preflight_abort_does_not_partially_launch(self):
        # Kimi could launch, but ZCode has an existing session without an endpoint.
        with (
            patch.object(launcher, "endpoint_ready", return_value=False),
            patch.object(launcher, "running_app", side_effect=[False, True]),
            patch.object(launcher.subprocess, "Popen") as spawn,
            contextlib.redirect_stdout(io.StringIO()),
        ):
            with self.assertRaisesRegex(RuntimeError, "already running"):
                launcher.launch_apps(list(self.registry))
        spawn.assert_not_called()

    def test_launch_is_loopback_minimized_and_does_not_change_profile(self):
        with (
            patch.object(launcher, "endpoint_ready", side_effect=[False, True]),
            patch.object(launcher, "running_app", return_value=False),
            patch.object(launcher.subprocess, "Popen") as spawn,
            contextlib.redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(launcher.launch_apps(["kimi"]), 0)
        command = spawn.call_args.args[0]
        self.assertEqual(
            command,
            [
                str(self.registry["kimi"][0]),
                "--remote-debugging-port=51001",
                "--remote-debugging-address=127.0.0.1",
            ],
        )
        self.assertEqual(spawn.call_args.kwargs["startupinfo"].wShowWindow, 7)
        self.assertFalse(any("user-data-dir" in arg for arg in command))

    def test_foreign_listener_rejected_before_http(self):
        listener = SimpleNamespace(
            status="LISTEN", laddr=SimpleNamespace(port=51001, ip="127.0.0.1"), pid=123
        )
        with (
            patch("psutil.net_connections", return_value=[listener]),
            patch(
                "psutil.Process",
                return_value=SimpleNamespace(
                    exe=lambda: str(self.root / "foreign.exe")
                ),
            ),
            patch.object(launcher, "build_opener") as opener,
        ):
            with self.assertRaisesRegex(DriverError, "another executable"):
                launcher.endpoint_ready("kimi")
        opener.assert_not_called()

    def test_autostart_logs_errors_without_waiting_for_input(self):
        with (
            patch.object(launcher, "ROOT", self.root),
            patch.object(
                launcher, "launch_apps", side_effect=DriverError("fixture failure")
            ),
            patch.object(launcher.time, "sleep") as sleep,
        ):
            self.assertEqual(launcher.main(["--autostart", "--check"]), 1)
        sleep.assert_not_called()
        self.assertIn(
            "fixture failure",
            (self.root / "data" / "startup.log").read_text(encoding="utf-8"),
        )


class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.box = Mailbox(self.root / "mail.db")
        self.box.register("kimi", "test-kimi", "Fixture", "fixture", guard_ready=True)
        self.box.bind("fixture-kimi", "kimi", "test-kimi")

    def test_tool_routing_preserves_input_and_default_dispatch(self):
        args = {"alias": "fixture-kimi", "body": "fixture text"}
        with patch(
            "desktop_delivery.deliver_wake", return_value={"submitted": True}
        ) as deliver:
            sent = invoke(self.box, "codex", "bridge_send", args)
        deliver.assert_called_once()
        self.assertEqual(args, {"alias": "fixture-kimi", "body": "fixture text"})
        self.assertEqual(sent["state"], "queued")
        received_args = {"caller_session_id": "test-kimi", "message_id": sent["id"]}
        self.assertEqual(
            invoke(self.box, "kimi", "bridge_inbox", received_args)[0]["state"],
            "delivered",
        )
        self.assertEqual(received_args["caller_session_id"], "test-kimi")
        with self.assertRaises(BridgeError):
            invoke(self.box, "kimi", "bridge_send", args)

    def test_explicit_streams_keep_notifications_silent_and_errors_recoverable(self):
        frames = [
            '{"method":"notifications/initialized"}',
            "bad json",
            '{"id":1,"method":"missing"}',
            '{"id":2,"method":"ping"}',
        ]
        output = io.StringIO()
        serve(
            self.box,
            "codex",
            input_stream=io.StringIO("\n".join(frames) + "\n"),
            output_stream=output,
        )
        responses = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual(len(responses), 3)
        self.assertEqual(responses[1]["error"]["code"], -32601)
        self.assertEqual(responses[2], {"jsonrpc": "2.0", "id": 2, "result": {}})

    def test_package_and_legacy_cli_agree(self):
        outputs = []
        for entry in ([str(ROOT / "bridge.py")], ["-m", "harness_bridge"]):
            result = subprocess.run(
                [sys.executable, *entry, "--db", str(self.root / "empty.db"), "peers"],
                cwd=ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=10,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            outputs.append(json.loads(result.stdout))
        self.assertEqual(outputs, [{"peers": [], "bindings": []}] * 2)

    @unittest.skipUnless(os.name == "nt", "Windows CMD entry points")
    def test_cmd_entry_points_use_this_copy_and_preserve_exit_codes(self):
        environment = dict(os.environ, HARNESS_BRIDGE_PYTHON=sys.executable)
        for folder in ("bridge", "launcher"):
            script = ROOT.parent / folder / "start_background.cmd"
            for flag, expected in (("--help", 0), ("--unknown-option", 2)):
                with self.subTest(folder=folder, flag=flag):
                    result = subprocess.run(
                        ["cmd", "/d", "/c", str(script), flag],
                        cwd=self.root,
                        env=environment,
                        capture_output=True,
                        text=True,
                        timeout=10,
                    )
                    self.assertEqual(result.returncode, expected, result.stderr)
                    if expected == 0:
                        self.assertIn("--autostart", result.stdout)


class BackgroundInstallerTests(unittest.TestCase):
    def test_cua_registration_preserves_config_without_a_server_table(self):
        script = ROOT.parent / "integrations" / "cua" / "install_codex.py"
        spec = importlib.util.spec_from_file_location("fixture_cua_bytes", script)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        original = 'model="fixture"\n# 手工配置保留\n'.encode("utf-8")
        result = module.registration_bytes(original, module.EXPECTED)
        self.assertTrue(result.startswith(original))
        self.assertEqual(module.registration_bytes(result, module.EXPECTED), result)
        conflict = dict(module.EXPECTED, command="foreign-driver")
        with self.assertRaisesRegex(RuntimeError, "refusing overwrite"):
            module.registration_bytes(result, conflict)

    def test_fresh_copy_receipt_directory_and_idempotency(self):
        script = ROOT.parent / "integrations" / "cua" / "install_codex.py"
        spec = importlib.util.spec_from_file_location("fixture_cua_installer", script)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "config.toml"
            original = b'model="fixture"\n[mcp_servers.other]\ncommand="preserve"\n'
            config.write_bytes(original)
            binary = root / "fixture.exe"
            binary.touch()
            policy = root / "policy.yaml"
            policy.write_text("fixture", encoding="utf-8")
            expected = dict(module.EXPECTED, command=str(binary))
            with (
                patch.multiple(
                    module,
                    ROOT=root,
                    CONFIG=config,
                    BINARY=binary,
                    POLICY=policy,
                    EXPECTED=expected,
                ),
                contextlib.redirect_stdout(io.StringIO()),
            ):
                module.main()
                installed = config.read_bytes()
                module.main()
            self.assertEqual(config.read_bytes(), installed)
            self.assertTrue(installed.startswith(original))
            receipt = json.loads(
                (root / "verification" / "install-receipt.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertTrue(receipt["existing_configuration_preserved"])
            self.assertFalse(receipt["codex_runtime_reload_verified"])


if __name__ == "__main__":
    unittest.main()
