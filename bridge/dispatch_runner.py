"""Fixed background dispatcher invoked by MCP, never by model-generated shell.

The only child command is this bridge's Python entry point, with an existing
message ID and the server's database. No caller-supplied command, script, app,
network address or recovery override is accepted here. Host approvals still
apply to the MCP tool; annotations describe behavior rather than grant access.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

from harness_courier.tool_names import legacy_name

READ_TOOLS = frozenset(
    {"bridge_peers", "bridge_status", "bridge_wait", "bridge_agent_status"}
)
KNOWN_TOOLS = READ_TOOLS | {
    "bridge_bind",
    "bridge_send",
    "bridge_dispatch",
    "bridge_inbox",
    "bridge_ack",
    "bridge_reply",
}
DISPATCH_TIMEOUT = 45


def tool_annotations(name: str) -> dict[str, bool]:
    """Truthful MCP hints: sending and inbox delivery are never marked read-only."""
    name = legacy_name(name)
    if name not in KNOWN_TOOLS:
        raise ValueError("Unknown bridge tool")
    return {
        "readOnlyHint": name in READ_TOOLS,
        "destructiveHint": name == "bridge_bind",
        "idempotentHint": name in READ_TOOLS | {"bridge_ack", "bridge_reply"},
        "openWorldHint": name in {"bridge_send", "bridge_dispatch"},
    }


def _dispatch_failure(
    box: Any, message_id: str, reason: str, *, uncertain: bool
) -> dict:
    """Keep the original ID queued/delivered; never fabricate a receiver ACK."""
    with box.connect() as db:
        db.execute(
            "UPDATE messages SET dispatch_error=? WHERE id=? AND state IN ('queued','delivered')",
            (reason[:1000], message_id),
        )
    return {
        **box.status(message_id),
        "desktop_delivery": {
            "submitted": None if uncertain else False,
            "reason": reason,
            "outcome_uncertain": uncertain,
            "note": "Query this original message ID before considering another dispatch",
        },
    }


def dispatch_fresh(
    box: Any, message_id: str, *, allow_busy_navigation=True, priority=True
) -> dict:
    """Perform bounded background dispatch inside the MCP integration process.

    A fresh child loads current disk code and preserves the old receipt/journal
    guards. ACKed or terminal messages return without spawning a process.
    """
    if not isinstance(message_id, str) or not re.fullmatch(
        r"msg_[a-f0-9]{32}", message_id
    ):
        raise ValueError("Invalid bridge message ID")
    if type(allow_busy_navigation) is not bool or type(priority) is not bool:
        raise ValueError("Delivery options must be booleans")
    current = box.status(message_id)
    if current["state"] != "queued" and not (
        current["state"] == "delivered" and current["dispatch_error"]
    ):
        return current
    command = [
        sys.executable,
        str(Path(__file__).with_name("dispatch_background.py")),
        "--db",
        str(box.path.resolve()),
        message_id,
    ]
    command.append(
        "--allow-busy-navigation"
        if allow_busy_navigation
        else "--no-allow-busy-navigation"
    )
    command.append("--priority" if priority else "--no-priority")
    try:
        completed = subprocess.run(
            command,
            shell=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            timeout=DISPATCH_TIMEOUT,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except subprocess.TimeoutExpired:
        return _dispatch_failure(
            box,
            message_id,
            "Background dispatch timed out; submission outcome unknown",
            uncertain=True,
        )
    except OSError:
        return _dispatch_failure(
            box,
            message_id,
            "Cannot start the fixed background dispatcher",
            uncertain=False,
        )
    try:
        result = json.loads(completed.stdout)
    except (ValueError, TypeError):
        return _dispatch_failure(
            box,
            message_id,
            "Background dispatcher returned an invalid response; inspect the original receipt",
            uncertain=True,
        )
    if not isinstance(result, dict):
        return _dispatch_failure(
            box,
            message_id,
            "Background dispatcher response must be an object",
            uncertain=True,
        )
    if "error" in result:
        # Child errors are bounded domain errors. Never echo raw stderr/logs.
        return _dispatch_failure(
            box, message_id, str(result["error"])[:1000], uncertain=True
        )
    identity = ("id", "harness", "session_id", "binding_revision")
    if any(result.get(key) != current.get(key) for key in identity):
        return _dispatch_failure(
            box,
            message_id,
            "Background dispatcher response identity mismatch",
            uncertain=True,
        )
    if completed.returncode != 0 and not result.get("dispatch_error"):
        return _dispatch_failure(
            box,
            message_id,
            "Background dispatcher exited without a confirmed outcome",
            uncertain=True,
        )
    return result
