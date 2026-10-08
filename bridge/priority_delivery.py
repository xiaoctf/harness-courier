"""Native priority actions for one verified composer and one opaque wake marker.

Never click a generic Stop button or clear another message's queue item. These
actions request priority in the harness; only receiver receipts prove consumption.
"""

from __future__ import annotations

import time

from cu_client import DriverError


def steer_kimi(client, binding, marker):
    """Try current-draft priority even when an earlier busy probe was false.

    The native handler evaluates activity at key delivery time. On idle compact
    composers Ctrl+Enter can be a no-op; return False only after observing the
    exact unchanged draft, so the caller can submit normally after a fresh guard.
    A lost key response or changed draft never permits a fallback submit.
    """
    ready = client.evaluate("keyboard_submit_ready", binding, marker)
    if ready.get("error") or not ready.get("ready"):
        raise DriverError("Priority delivery stopped: composer changed before steer")
    for kind in ("keyDown", "keyUp"):
        client.call(
            "Input.dispatchKeyEvent",
            {
                "type": kind,
                "key": "Enter",
                "code": "Enter",
                "windowsVirtualKeyCode": 13,
                "modifiers": 2,
            },
        )
    deadline = time.monotonic() + 1
    while True:
        outcome = client.evaluate("observe", binding, marker)
        if not outcome.get("matches") or outcome.get("editor_count") != 1:
            raise DriverError(
                "Priority target changed after shortcut; outcome uncertain"
            )
        if outcome.get("draft_empty") is True:
            return True
        if outcome.get("marker_present") is not True:
            raise DriverError(
                "Priority draft changed after shortcut; outcome uncertain"
            )
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.05)


def promote_zcode(client, binding, marker, *, require_queue=True):
    """Promote exactly this marker, leaving all other queued messages intact."""
    deadline = time.monotonic() + 3
    while True:
        observation = client.evaluate("priority_queue_ready", binding, marker)
        if observation.get("error"):
            raise DriverError("Priority queue stopped: " + observation["error"])
        if observation.get("ready"):
            break
        if time.monotonic() >= deadline:
            if not require_queue:
                return {
                    "priority_requested": True,
                    "action": "zcode_no_matching_queue_observed",
                }
            raise DriverError("Priority queue item unavailable; no other item promoted")
        time.sleep(0.1)
    result = client.evaluate("priority_queue_submit", binding, marker)
    if result.get("error") or not result.get("priority_requested"):
        raise DriverError("Priority queue changed before promotion; outcome uncertain")
    # Do not retry the click if its asynchronous result is delayed or lost.
    return {"priority_requested": True, "action": "zcode_send_queued_now"}
