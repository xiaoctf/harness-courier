"""Native priority actions for one verified composer and one opaque wake marker.

Never click a generic Stop button or clear another message's queue item. These
actions request priority in the harness; only receiver receipts prove consumption.
"""

from __future__ import annotations

import time

from cu_client import DriverError


def steer_kimi(client, binding, marker):
    """Ctrl+Enter targets the current draft; Ctrl+S can steer an old queue head."""
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


def promote_zcode(client, binding, marker):
    """Promote exactly this marker, leaving all other queued messages intact."""
    deadline = time.monotonic() + 3
    while True:
        observation = client.evaluate("priority_queue_ready", binding, marker)
        if observation.get("error"):
            raise DriverError("Priority queue stopped: " + observation["error"])
        if observation.get("ready"):
            break
        if time.monotonic() >= deadline:
            raise DriverError("Priority queue item unavailable; no other item promoted")
        time.sleep(0.1)
    result = client.evaluate("priority_queue_submit", binding, marker)
    if result.get("error") or not result.get("priority_requested"):
        raise DriverError("Priority queue changed before promotion; outcome uncertain")
    # Do not retry the click if its asynchronous result is delayed or lost.
    return {"priority_requested": True, "action": "zcode_send_queued_now"}
