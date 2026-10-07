import io
import json
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from desktop_delivery import (
    compose_index,
    exact_kimi_session,
    input_is_empty,
    sidebar_title_targets,
    zcode_header_title,
)
from receiver_hook import process
from setup_bridge import install

from bridge import BridgeError, Mailbox, invoke, serve, tool_specs


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.temp_root = Path(__file__).resolve().parent / "test-tmp"
        self.temp_root.mkdir(exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(dir=self.temp_root)
        self.db = Path(self.tmp.name) / "mail.sqlite3"
        self.box = Mailbox(self.db)
        for harness, sid, title in [
            ("kimi", "kimi-A", "Kimi A"),
            ("kimi", "kimi-B", "Kimi B"),
            ("zcode", "zcode-A", "ZCode A"),
        ]:
            self.box.register(
                harness,
                sid,
                title,
                str(Path(self.tmp.name) / harness),
                guard_ready=True,
            )
        self.box.bind("kimi", "kimi", "kimi-A")
        self.box.bind("zcode", "zcode", "zcode-A")

    def tearDown(self):
        self.assertTrue(
            Path(self.tmp.name).resolve().is_relative_to(self.temp_root.resolve())
        )
        self.tmp.cleanup()

    def queued(self):
        return self.box.send(
            "kimi", "只回复通信测试通过", sender_session_id="codex-source"
        )["id"]

    def test_round_trip_survives_process_restart(self):
        mid = self.queued()
        self.box = Mailbox(self.db)
        self.assertEqual(self.box.receive("kimi", "kimi-A")[0]["id"], mid)
        self.box.acknowledge("kimi", "kimi-A", mid)
        self.box = Mailbox(self.db)
        result = self.box.reply("kimi", "kimi-A", mid, "收到，测试通过")
        self.assertEqual(result["state"], "completed")
        self.assertEqual(result["sender_session_id"], "codex-source")
        self.assertEqual(Mailbox(self.db).status(mid)["result"], "收到，测试通过")

    def test_other_chat_cannot_receive_or_ack(self):
        mid = self.queued()
        self.assertEqual(self.box.receive("kimi", "kimi-B"), [])
        with self.assertRaises(BridgeError):
            self.box.receive("kimi", "kimi-B", mid)
        with self.assertRaises(BridgeError):
            self.box.acknowledge("kimi", "kimi-B", mid)
        with self.assertRaises(BridgeError):
            self.box.reply("zcode", "zcode-A", mid, "wrong")
        self.assertEqual(self.box.status(mid)["state"], "queued")

    def test_bind_requires_actual_registered_hook(self):
        self.box.register("kimi", "unready", "Unready", "E:/test")
        with self.assertRaises(BridgeError):
            self.box.bind("unknown", "kimi", "unready")
        with self.assertRaises(BridgeError):
            self.box.bind("unknown", "kimi", "missing")

    def test_rebinding_never_moves_old_messages(self):
        mid = self.queued()
        with self.assertRaises(BridgeError):
            self.box.bind("kimi", "kimi", "kimi-B")
        self.box.bind("kimi", "kimi", "kimi-B", replace=True)
        self.assertEqual(self.box.status(mid)["session_id"], "kimi-A")
        self.assertEqual(self.box.receive("kimi", "kimi-B"), [])
        with self.assertRaises(BridgeError):
            self.box.dispatch(mid)

    def test_idempotent_binding_does_not_change_revision(self):
        before = self.box.binding("kimi")
        after = self.box.bind("kimi", "kimi", "kimi-A")
        self.assertEqual(before["revision"], after["revision"])

    def test_cannot_ack_before_receiver_delivery(self):
        mid = self.queued()
        with self.assertRaises(BridgeError):
            self.box.acknowledge("kimi", "kimi-A", mid)

    def test_duplicate_receipts_do_not_regress_terminal_state(self):
        mid = self.queued()
        self.box.receive("kimi", "kimi-A")
        self.box.reply("kimi", "kimi-A", mid, "OK")
        self.assertEqual(
            self.box.acknowledge("kimi", "kimi-A", mid)["state"], "completed"
        )
        self.assertEqual(
            self.box.reply("kimi", "kimi-A", mid, "OK")["state"], "completed"
        )
        with self.assertRaises(BridgeError):
            self.box.reply("kimi", "kimi-A", mid, "different")

    def test_gui_submission_is_never_a_receiver_ack(self):
        mid = self.queued()
        with patch("desktop_delivery.deliver_wake", return_value={"submitted": True}):
            result = self.box.dispatch(mid)
        self.assertEqual(result["state"], "queued")
        self.assertIsNone(result["acknowledged_at"])

    def test_gui_failure_keeps_pending_message(self):
        mid = self.queued()
        with patch(
            "desktop_delivery.deliver_wake", side_effect=RuntimeError("wrong session")
        ):
            result = self.box.dispatch(mid)
        self.assertEqual(result["state"], "queued")
        self.assertEqual(result["dispatch_error"], "wrong session")

    def test_retry_after_sessionstart_delivery_and_editor_loading_failure(self):
        mid = self.queued()

        def loading_failure(*args, **kwargs):
            self.box.receive("kimi", "kimi-A", mid)
            raise RuntimeError("editor still loading")

        with patch("desktop_delivery.deliver_wake", side_effect=loading_failure):
            self.assertEqual(self.box.dispatch(mid)["state"], "delivered")
        with patch(
            "desktop_delivery.deliver_wake", return_value={"submitted": True}
        ) as wake:
            result = self.box.dispatch(mid)
            wake.assert_called_once()
            self.assertIsNone(result["dispatch_error"])
            self.box.acknowledge("kimi", "kimi-A", mid)
            self.box.dispatch(mid)
            wake.assert_called_once()

    def test_hook_rejects_wrong_session_without_revealing_body(self):
        mid = self.queued()
        output, code = process(
            self.box,
            "kimi",
            {
                "hook_event_name": "UserPromptSubmit",
                "session_id": "kimi-B",
                "session_title": "Kimi B",
                "cwd": "E:/test",
                "prompt": f"[HARNESS_BRIDGE_WAKE:{mid}]",
            },
        )
        self.assertEqual(code, 2)
        self.assertNotIn("只回复通信测试通过", json.dumps(output, ensure_ascii=False))
        self.assertEqual(self.box.status(mid)["state"], "queued")

    def test_hook_delivers_to_exact_target_then_agent_acks(self):
        mid = self.queued()
        output, code = process(
            self.box,
            "kimi",
            {
                "hook_event_name": "UserPromptSubmit",
                "session_id": "kimi-A",
                "session_title": "Kimi A",
                "cwd": "E:/test",
                "prompt": f"[HARNESS_BRIDGE_WAKE:{mid}]",
            },
        )
        self.assertEqual(code, 0)
        self.assertIn(mid, output["additional_context"])
        self.assertEqual(self.box.status(mid)["state"], "delivered")
        self.assertIsNone(self.box.status(mid)["acknowledged_at"])

    def test_contentpart_wake_selects_exact_id_not_oldest_pending(self):
        old = self.queued()
        fresh = self.queued()
        output, code = process(
            self.box,
            "kimi",
            {
                "hook_event_name": "UserPromptSubmit",
                "session_id": "kimi-A",
                "prompt": [{"type": "text", "text": f"[HARNESS_BRIDGE_WAKE:{fresh}]"}],
            },
        )
        self.assertEqual(code, 0)
        self.assertIn(fresh, output["additional_context"])
        self.assertNotIn(old, output["additional_context"])
        self.assertEqual(self.box.status(fresh)["state"], "delivered")
        self.assertEqual(self.box.status(old)["state"], "queued")

    def test_unknown_prompt_shape_does_not_dequeue_pending(self):
        mid = self.queued()
        for prompt in (
            {"unexpected": "text"},
            [{"type": "image_url", "url": "private-fixture"}],
            42,
        ):
            output, code = process(
                self.box,
                "kimi",
                {
                    "hook_event_name": "UserPromptSubmit",
                    "session_id": "kimi-A",
                    "prompt": prompt,
                },
            )
            self.assertEqual(code, 0)
            self.assertEqual(self.box.status(mid)["state"], "queued")
            self.assertNotIn("private-fixture", json.dumps(output))

    def test_automatic_hook_does_not_reinject_acknowledged_work(self):
        old = self.queued()
        self.box.receive("kimi", "kimi-A", old)
        self.box.acknowledge("kimi", "kimi-A", old)
        fresh = self.queued()
        output, code = process(
            self.box,
            "kimi",
            {
                "hook_event_name": "UserPromptSubmit",
                "session_id": "kimi-A",
                "prompt": "Continue current review",
            },
        )
        self.assertEqual(code, 0)
        self.assertIn(fresh, output["additional_context"])
        self.assertNotIn(old, output["additional_context"])
        self.assertEqual(self.box.status(old)["state"], "acknowledged")

    def test_automatic_hook_with_only_acknowledged_work_is_identity_only(self):
        old = self.queued()
        self.box.receive("kimi", "kimi-A", old)
        self.box.acknowledge("kimi", "kimi-A", old)
        output, code = process(
            self.box,
            "kimi",
            {"hook_event_name": "SessionStart", "session_id": "kimi-A"},
        )
        self.assertEqual(code, 0)
        self.assertNotIn(old, output["additional_context"])
        self.assertEqual(self.box.receive("kimi", "kimi-A", old)[0]["id"], old)

    def test_unqualified_inbox_prioritizes_unacknowledged_message(self):
        old = self.queued()
        self.box.receive("kimi", "kimi-A", old)
        self.box.acknowledge("kimi", "kimi-A", old)
        fresh = self.queued()
        self.assertEqual(self.box.receive("kimi", "kimi-A", limit=1)[0]["id"], fresh)

    def test_terminal_wake_returns_explicit_no_reexecution_notice(self):
        old = self.queued()
        self.box.receive("kimi", "kimi-A", old)
        self.box.reply("kimi", "kimi-A", old, "historic result")
        fresh = self.queued()
        output, code = process(
            self.box,
            "kimi",
            {
                "hook_event_name": "UserPromptSubmit",
                "session_id": "kimi-A",
                "prompt": f"[HARNESS_BRIDGE_WAKE:{old}]",
            },
        )
        self.assertEqual(code, 0)
        self.assertIn("TERMINAL_WAKE_NO_REEXECUTION", output["additional_context"])
        self.assertNotIn("historic result", output["additional_context"])
        self.assertEqual(self.box.status(fresh)["state"], "queued")

    def test_zcode_hook_contract_blocks_wrong_target(self):
        mid = self.queued()
        output, code = process(
            self.box,
            "zcode",
            {
                "hook_event_name": "UserPromptSubmit",
                "session_id": "zcode-A",
                "cwd": "E:/test",
                "prompt": f"[HARNESS_BRIDGE_WAKE:{mid}]",
            },
        )
        self.assertEqual(code, 0)
        self.assertFalse(output["continue"])

    def test_unbound_hook_retains_only_metadata(self):
        output, code = process(
            self.box,
            "kimi",
            {
                "hook_event_name": "UserPromptSubmit",
                "session_id": "unbound",
                "session_title": "Unused",
                "cwd": "E:/test",
                "prompt": "private transcript fixture",
                "tool_input": {"private": "not retained"},
            },
        )
        self.assertEqual(output, {})
        with self.box.connect() as db:
            values = json.dumps([dict(r) for r in db.execute("SELECT * FROM peers")])
        self.assertNotIn("private transcript", values)
        self.assertNotIn("not retained", values)

    def test_timeout_does_not_fail_or_repeat_delivery(self):
        mid = self.queued()
        result = self.box.wait(mid, timeout=0)
        self.assertEqual(result["state"], "queued")
        with self.assertRaises(BridgeError):
            self.box.wait(mid, timeout=100)

    def test_concurrent_agents_do_not_mix_message_targets(self):
        errors = []

        def worker(alias, harness, sid):
            try:
                box = Mailbox(self.db)
                for i in range(8):
                    mid = box.send(alias, f"test-{i}")["id"]
                    box.receive(harness, sid, mid)
                    box.reply(harness, sid, mid, harness)
            except Exception as exc:
                errors.append(exc)

        threads = [
            threading.Thread(target=worker, args=x)
            for x in [("kimi", "kimi", "kimi-A"), ("zcode", "zcode", "zcode-A")]
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])
        with self.box.connect() as db:
            rows = db.execute("SELECT harness,result,state FROM messages").fetchall()
        self.assertEqual(len(rows), 16)
        self.assertTrue(
            all(r["harness"] == r["result"] and r["state"] == "completed" for r in rows)
        )

    def test_mcp_tools_are_scoped_by_harness(self):
        with self.assertRaises(BridgeError):
            invoke(self.box, "kimi", "bridge_bind", {})
        with self.assertRaises(BridgeError):
            invoke(self.box, "codex", "bridge_ack", {})
        self.assertIn("bridge_reply", [x["name"] for x in tool_specs("zcode")])

    def test_mcp_processes_share_persistent_results(self):
        mid = self.queued()
        requests = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": "bridge_inbox",
                    "arguments": {"caller_session_id": "kimi-A", "message_id": mid},
                },
            },
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {
                    "name": "bridge_ack",
                    "arguments": {"caller_session_id": "kimi-A", "message_id": mid},
                },
            },
            {
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": {
                    "name": "bridge_reply",
                    "arguments": {
                        "caller_session_id": "kimi-A",
                        "message_id": mid,
                        "body": "MCP roundtrip",
                    },
                },
            },
        ]
        result = subprocess.run(
            [
                sys.executable,
                str(Path(__file__).with_name("bridge.py")),
                "--db",
                str(self.db),
                "mcp",
                "--harness",
                "kimi",
            ],
            input="".join(json.dumps(r) + "\n" for r in requests),
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        responses = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual(len(responses), 4)
        self.assertFalse(responses[-1]["result"]["isError"])
        self.assertEqual(Mailbox(self.db).status(mid)["result"], "MCP roundtrip")

    def test_bad_mcp_json_does_not_break_next_request(self):
        output = io.StringIO()
        with (
            patch("sys.stdin", io.StringIO('bad json\n{"id":2,"method":"ping"}\n')),
            patch("sys.stdout", output),
        ):
            serve(self.box, "codex")
        lines = [json.loads(x) for x in output.getvalue().splitlines()]
        self.assertIn("error", lines[0])
        self.assertEqual(lines[1]["id"], 2)

    def test_offline_target_has_no_fallback(self):
        self.box.register("kimi", "kimi-A", "Kimi A", "E:/test", online=False)
        with self.assertRaises(BridgeError):
            self.queued()

    def test_kimi_identity_and_draft_guards(self):
        state = {
            "accessibility": {
                "tree": '[17] 文档 value="app://renderer/sessions/session_abc"\n[55] 组合框 name="消息输入框" class="ProseMirror"\n[56] 文本\n[57] 按钮 name="发送"'
            }
        }
        self.assertEqual(exact_kimi_session(state), "session_abc")
        self.assertEqual(compose_index(state, "kimi"), 55)
        self.assertTrue(input_is_empty(state, 55))
        state["accessibility"]["tree"] = state["accessibility"]["tree"].replace(
            "[56] 文本", '[56] 文本 name="unfinished draft"'
        )
        self.assertFalse(input_is_empty(state, 55))

    def test_zcode_title_is_read_from_header_not_message_body(self):
        state = {
            "accessibility": {
                "tree": '[173] 章节标题 class="@container/workspace-header relative"\n[174] 按钮 name="Project"\n[175] 标题 name="Target"\n[176] 文本 name="Target"'
            }
        }
        self.assertEqual(zcode_header_title(state), "Target")
        self.assertIsNone(
            zcode_header_title({"accessibility": {"tree": '[175] 标题 name="Target"'}})
        )

    def test_long_sidebar_title_click_stays_inside_visible_chat_row(self):
        state = {
            "accessibility": {
                "tree": '[56] 组 class="se" rect=9,252 272x38\n[57] 文本 name="Long title" rect=43,260 713x21\n[116] 章节标题 class="chat-header"'
            },
            "coordinate_space": {"image_width": 1568, "image_height": 929},
        }
        target = sidebar_title_targets(state, "Long title")[0]
        self.assertTrue(43 < target["x"] < 281)
        self.assertTrue(260 < target["y"] < 281)
        self.assertEqual(sidebar_title_targets(state, "Another title"), [])

    def test_hidden_sidebar_titles_are_not_clicked(self):
        for y in (-50, 950):
            state = {
                "accessibility": {
                    "tree": f'[56] 组 class="se" rect=9,{y} 272x38\n[57] 文本 name="Target" rect=43,{y} 300x21'
                },
                "coordinate_space": {"image_width": 1568, "image_height": 929},
            }
            self.assertEqual(sidebar_title_targets(state, "Target"), [])

    def test_config_install_preserves_existing_entries_and_is_idempotent(self):
        home = Path(self.tmp.name) / "fake-home"
        (home / ".codex").mkdir(parents=True)
        (home / ".kimi-code").mkdir()
        (home / ".zcode" / "cli").mkdir(parents=True)
        (home / ".codex" / "config.toml").write_text(
            'model="unchanged"\n[mcp_servers.existing]\ncommand="preserve"\n',
            encoding="utf-8",
        )
        (home / ".kimi-code" / "config.toml").write_text(
            'model="unchanged"\n[[hooks]]\nevent="Stop"\ncommand="existing-check"\n',
            encoding="utf-8",
        )
        (home / ".kimi-code" / "mcp.json").write_text(
            json.dumps(
                {"mcpServers": {"existing": {"command": "preserve"}}, "other": "keep"}
            ),
            encoding="utf-8",
        )
        original = {
            "model": "unchanged",
            "mcp": {"servers": {"existing": {"command": "preserve"}}},
            "hooks": {
                "enabled": True,
                "events": {
                    "Stop": [{"hooks": [{"type": "process", "command": "old-check"}]}]
                },
            },
        }
        (home / ".zcode" / "cli" / "config.json").write_text(
            json.dumps(original), encoding="utf-8"
        )
        first = install(home, Path(sys.executable), True)
        self.assertTrue(first["applied"])
        second = install(home, Path(sys.executable), True)
        self.assertTrue(all(not x["changed"] for x in second["configuration"]))
        new = json.loads(
            (home / ".zcode" / "cli" / "config.json").read_text(encoding="utf-8")
        )
        self.assertEqual(new["model"], original["model"])
        self.assertEqual(
            new["hooks"]["events"]["Stop"], original["hooks"]["events"]["Stop"]
        )
        self.assertEqual(
            new["mcp"]["servers"]["existing"], original["mcp"]["servers"]["existing"]
        )

    def test_install_does_not_enable_unrelated_disabled_hooks(self):
        home = Path(self.tmp.name) / "fake-home"
        (home / ".zcode" / "cli").mkdir(parents=True)
        (home / ".zcode" / "cli" / "config.json").write_text(
            json.dumps(
                {"hooks": {"enabled": False, "events": {"Stop": [{"hooks": []}]}}}
            ),
            encoding="utf-8",
        )
        with self.assertRaises(ValueError):
            install(home, Path(sys.executable), True)
        self.assertFalse((home / ".codex" / "config.toml").exists())

    def test_real_hook_subprocess_emits_correct_protocol(self):
        mid = self.queued()
        payload = {
            "hook_event_name": "UserPromptSubmit",
            "session_id": "kimi-A",
            "session_title": "Kimi A",
            "cwd": "E:/test",
            "prompt": f"[HARNESS_BRIDGE_WAKE:{mid}]",
        }
        result = subprocess.run(
            [
                sys.executable,
                str(Path(__file__).with_name("receiver_hook.py")),
                "--db",
                str(self.db),
                "--harness",
                "kimi",
            ],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("caller_session_id=kimi-A", result.stdout)
        self.assertEqual(self.box.status(mid)["state"], "delivered")

    def test_fresh_hook_process_skips_old_ack_and_delivers_current(self):
        old = self.queued()
        self.box.receive("kimi", "kimi-A", old)
        self.box.acknowledge("kimi", "kimi-A", old)
        fresh = self.queued()
        payload = {
            "hook_event_name": "UserPromptSubmit",
            "session_id": "kimi-A",
            "prompt": "Continue the current bounded review",
        }
        result = subprocess.run(
            [
                sys.executable,
                str(Path(__file__).with_name("receiver_hook.py")),
                "--db",
                str(self.db),
                "--harness",
                "kimi",
            ],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(fresh, result.stdout)
        self.assertNotIn(old, result.stdout)
        self.assertEqual(self.box.status(old)["state"], "acknowledged")
        self.assertEqual(self.box.status(fresh)["state"], "delivered")

    def test_hook_output_stays_under_zcode_output_limit(self):
        body = "中" * 6000
        for _ in range(3):
            self.box.send("zcode", body)
        output, code = process(
            self.box,
            "zcode",
            {
                "hook_event_name": "UserPromptSubmit",
                "session_id": "zcode-A",
                "cwd": "E:/test",
                "prompt": "receive",
            },
        )
        self.assertEqual(code, 0)
        self.assertLess(
            len(json.dumps(output, ensure_ascii=False).encode("utf-8")), 32768
        )
        with self.box.connect() as db:
            self.assertEqual(
                db.execute(
                    "SELECT count(*) FROM messages WHERE state='queued'"
                ).fetchone()[0],
                2,
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
