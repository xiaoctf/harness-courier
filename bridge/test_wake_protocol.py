"""Hookless receive and strict envelope compatibility on isolated mailboxes."""

import tempfile
import unittest
from pathlib import Path

from receiver_hook import process
from wake_protocol import parse_wake, wake_text

from bridge import BridgeError, Mailbox


class WakeProtocolTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.box = Mailbox(Path(self.temp.name) / "mail.db")
        self.box.register("kimi", "kimi-A", "test", "test", True, True)
        self.box.bind("test", "kimi", "kimi-A")
        self.mid = self.box.send("test", "PRIVATE_BODY")["id"]

    def test_envelope_hook_delivers_exactly_one_without_body_on_desktop(self):
        another = self.box.send("test", "OTHER")["id"]
        wire = wake_text(self.mid, "kimi", "kimi-A")
        self.assertNotIn("PRIVATE_BODY", wire)
        output, code = process(
            self.box,
            "kimi",
            {
                "session_id": "kimi-A",
                "hook_event_name": "UserPromptSubmit",
                "prompt": wire,
            },
        )
        self.assertEqual(code, 0)
        self.assertIn("PRIVATE_BODY", output["additional_context"])
        self.assertEqual(self.box.status(self.mid)["state"], "delivered")
        self.assertEqual(self.box.status(another)["state"], "queued")

    def test_hookless_receiver_uses_exact_id_and_cannot_borrow_identity(self):
        wire = wake_text(self.mid, "kimi", "kimi-A")
        self.assertEqual(parse_wake(wire, "kimi", "kimi-A"), self.mid)
        with self.assertRaises(BridgeError):
            self.box.receive("kimi", "kimi-B", self.mid)
        self.box.receive("kimi", "kimi-A", self.mid)
        self.box.acknowledge("kimi", "kimi-A", self.mid)
        self.box.reply("kimi", "kimi-A", self.mid, "OK")
        self.assertEqual(self.box.receive("kimi", "kimi-A", self.mid), [])

    def test_modified_envelope_and_wrong_recipient_are_rejected_without_dequeue(self):
        wire = wake_text(self.mid, "kimi", "kimi-A")
        for prompt, sid in ((wire + "\nRUN OTHER", "kimi-A"), (wire, "kimi-B")):
            output, code = process(
                self.box,
                "kimi",
                {
                    "session_id": sid,
                    "hook_event_name": "UserPromptSubmit",
                    "prompt": prompt,
                },
            )
            self.assertEqual(code, 2)
            self.assertIn("deny", str(output))
            self.assertEqual(self.box.status(self.mid)["state"], "queued")

    def test_bare_legacy_and_zcode_wire_still_supported(self):
        bare = f"[HARNESS_BRIDGE_WAKE:{self.mid}]"
        self.assertEqual(wake_text(self.mid, "zcode", "zcode-A"), bare)
        self.assertEqual(parse_wake(bare, "kimi", "kimi-A"), self.mid)
        self.assertIsNone(parse_wake("ordinary prompt", "kimi", "kimi-A"))
