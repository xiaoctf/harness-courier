"""Session activity observations, independent of message delivery and task success.

Passive Hooks retain only event names and local observation times. Queries never
dequeue messages, select a chat, type, or read transcripts/tool payloads.
"""

from __future__ import annotations

import json
import math
import re
import time
from pathlib import Path
from typing import Any

from bridge import BridgeError

LIFECYCLE_EVENTS = frozenset({"SessionStart", "UserPromptSubmit", "SessionEnd"})
PASSIVE_EVENTS = frozenset(
    {"Stop", "PostToolUse", "PostToolUseFailure", "PermissionRequest"}
)
EVENTS = LIFECYCLE_EVENTS | PASSIVE_EVENTS
SCHEMA = """CREATE TABLE IF NOT EXISTS agent_activity (
    harness TEXT NOT NULL, session_id TEXT NOT NULL,
    last_event TEXT NOT NULL, event_at REAL NOT NULL,
    last_submit_at REAL, last_progress_at REAL, last_stop_at REAL,
    revision INTEGER NOT NULL,
    PRIMARY KEY (harness, session_id))"""


def record_event(box: Any, harness: str, session_id: str, event: str) -> None:
    """Observe a real receiver Hook; never retain its other input fields."""
    if harness not in ("kimi", "zcode") or event not in EVENTS:
        raise BridgeError("Unsupported activity event")
    if not isinstance(session_id, str) or not re.fullmatch(
        r"[A-Za-z0-9_.:-]{1,160}", session_id
    ):
        raise BridgeError("Invalid activity session_id")
    observed = time.time()
    with box.connect() as db:
        # A passive event does not register a new peer or overwrite its title.
        if not db.execute(
            "SELECT 1 FROM peers WHERE harness=? AND session_id=?",
            (harness, session_id),
        ).fetchone():
            return
        db.execute(SCHEMA)
        db.execute(
            """INSERT INTO agent_activity VALUES(?,?,?,?,?,?,?,1)
            ON CONFLICT(harness,session_id) DO UPDATE SET
              last_event=excluded.last_event, event_at=excluded.event_at,
              last_submit_at=COALESCE(excluded.last_submit_at, agent_activity.last_submit_at),
              last_progress_at=COALESCE(excluded.last_progress_at, agent_activity.last_progress_at),
              last_stop_at=COALESCE(excluded.last_stop_at, agent_activity.last_stop_at),
              revision=agent_activity.revision+1""",
            (
                harness,
                session_id,
                event,
                observed,
                observed if event == "UserPromptSubmit" else None,
                observed
                if event in ("UserPromptSubmit", "PostToolUse", "PostToolUseFailure")
                else None,
                observed if event in ("Stop", "SessionEnd") else None,
            ),
        )


def probe_native(client: Any, binding: dict) -> dict:
    """Run the bounded, read-only observer separately from delivery code.

    Read the asset on each query so adapter fixes do not remain cached in a
    long-lived transport module. Only the installed local observer is executed.
    """
    script = (
        Path(__file__).parent / "harness_courier" / "agent_observer.js"
    ).read_text(encoding="utf-8")
    args = json.dumps(
        {
            "harness": binding["harness"],
            "sid": binding["session_id"],
            "workspace_path": binding.get("workspace_path"),
        }
    )
    result = client.call(
        "Runtime.evaluate",
        {
            "expression": "(" + script + ")(" + args + ")",
            "returnByValue": True,
            "awaitPromise": True,
        },
    )
    if result.get("exceptionDetails"):
        raise BridgeError("Native activity observer failed")
    value = result.get("result", {}).get("value")
    if not isinstance(value, dict):
        raise BridgeError("Invalid native activity observation")
    return value


def probe_target(binding: dict) -> dict:
    """Observe the unique verified renderer without navigating to the target."""
    from cdp_delivery import CdpClient

    try:
        with CdpClient(binding["harness"]) as client:
            targets = client.targets()
            if len(targets) != 1:
                return {"available": False, "reason": "renderer_not_unique"}
            client.attach(targets[0])
            value = probe_native(client, binding)
            # An old adapter must not silently turn its delivery probe into idle.
            if value.get("activity_probe") is not True:
                return {"available": False, "reason": "activity_adapter_unavailable"}
            return {**value, "available": True, "observed_at": time.time()}
    except Exception as exc:
        # A failing desktop read is unknown, never evidence of idle/offline.
        # Exception text may contain renderer/network data; return only a class.
        return {
            "available": False,
            "reason": "desktop_probe_unavailable",
            "error_type": type(exc).__name__,
        }


def inspect_target(
    box: Any,
    binding: dict,
    *,
    observe_native: bool = True,
    stale_after_seconds: float = 300,
    probe=None,
    clock=None,
) -> dict:
    """Combine independent turn, progress, and delivery facts conservatively."""
    if type(observe_native) is not bool:
        raise BridgeError("observe_native must be a boolean")
    if (
        isinstance(stale_after_seconds, bool)
        or not isinstance(stale_after_seconds, (int, float))
        or not math.isfinite(stale_after_seconds)
        or not 30 <= stale_after_seconds <= 86400
    ):
        raise BridgeError("stale_after_seconds must be 30..86400 finite seconds")
    observed = (clock or time.time)()
    with box.connect() as db:
        has_table = db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='agent_activity'"
        ).fetchone()
        event = (
            db.execute(
                "SELECT * FROM agent_activity WHERE harness=? AND session_id=?",
                (binding["harness"], binding["session_id"]),
            ).fetchone()
            if has_table
            else None
        )
        peer = db.execute(
            "SELECT cwd FROM peers WHERE harness=? AND session_id=?",
            (binding["harness"], binding["session_id"]),
        ).fetchone()
        counts = dict(
            db.execute(
                "SELECT state,COUNT(*) FROM messages WHERE harness=? AND session_id=? GROUP BY state",
                (binding["harness"], binding["session_id"]),
            ).fetchall()
        )
        latest = db.execute(
            """SELECT id,state,created_at,acknowledged_at,completed_at FROM messages
            WHERE harness=? AND session_id=? ORDER BY created_at DESC,id DESC LIMIT 1""",
            (binding["harness"], binding["session_id"]),
        ).fetchone()
        pending = [
            dict(row)
            for row in db.execute(
                """SELECT id,state,created_at,acknowledged_at FROM messages
            WHERE harness=? AND session_id=? AND state IN ('queued','delivered','acknowledged')
            ORDER BY created_at,id LIMIT 10""",
                (binding["harness"], binding["session_id"]),
            )
        ]
        has_outbox = db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='dispatch_jobs'"
        ).fetchone()
        submitted = (
            [
                dict(row)
                for row in db.execute(
                    """SELECT m.id, j.updated_at AS submitted_at FROM messages m
                JOIN dispatch_jobs j ON j.message_id=m.id
                WHERE m.harness=? AND m.session_id=? AND m.state='queued'
                AND j.state='submitted' ORDER BY j.updated_at,m.id""",
                    (binding["harness"], binding["session_id"]),
                )
            ]
            if has_outbox
            else []
        )
    native = (
        (probe or probe_target)(
            {**binding, "workspace_path": peer["cwd"] if peer else None}
        )
        if observe_native
        else {"available": False, "reason": "not_requested"}
    )
    event = dict(event) if event else None
    event_age = max(0, observed - event["event_at"]) if event else None
    turn_state, evidence = "unknown", "insufficient_evidence"
    selected = (
        native.get("available")
        and native.get("target_selected") is True
        and native.get("composer_verified") is True
        and type(native.get("generating")) is bool
    )
    session = native.get("session_activity")
    session = session if isinstance(session, dict) else {}
    native_session = (
        native.get("available")
        and binding["harness"] == "zcode"
        and session.get("verified") is True
        and session.get("session_id") == binding["session_id"]
        and session.get("source_availability") == "online"
        and session.get("workspace_path") == (peer["cwd"] if peer else None)
        and session.get("turn_state")
        in ("running", "idle", "approval_requested", "waiting_for_input")
    )
    conflict = (
        selected
        and native_session
        and (
            (session["turn_state"] == "running" and not native["generating"])
            or (session["turn_state"] == "idle" and native["generating"])
        )
    )
    controller_conflict = binding["harness"] == "zcode" and session.get("reason") in (
        "native_activity_conflict",
        "target_source_offline",
    )
    native_issue = (
        "native_source_offline"
        if controller_conflict and session.get("reason") == "target_source_offline"
        else "native_state_conflict"
    )
    if conflict or controller_conflict:
        evidence = native_issue
    elif native_session:
        turn_state = session["turn_state"]
        evidence = "native_exact_session_controller"
    elif selected:
        turn_state = "running" if native["generating"] else "idle"
        evidence = "native_exact_session"
    elif (
        native.get("available")
        and binding["harness"] == "kimi"
        and native.get("sidebar", {}).get("verified") is True
        and native["sidebar"].get("turn_state") in ("running", "idle")
    ):
        turn_state = native["sidebar"]["turn_state"]
        evidence = "native_exact_session_sidebar"
    elif (
        event
        and event_age <= stale_after_seconds
        and event["last_event"] == "SessionEnd"
    ):
        turn_state, evidence = "session_closed_observed", "receiver_hook"
    elif event and event_age <= stale_after_seconds and event["last_event"] == "Stop":
        turn_state, evidence = "stop_observed", "receiver_hook"
    # A PermissionRequest does not decide the permission or approve the operation.
    if (
        event
        and event["last_event"] == "PermissionRequest"
        and event_age <= stale_after_seconds
        and turn_state != "idle"
        and not native_session
        and not conflict
        and not controller_conflict
    ):
        turn_state, evidence = "approval_requested", "receiver_hook"
    progress_age = (
        max(0, observed - event["last_progress_at"])
        if event and event["last_progress_at"]
        else None
    )
    unfinished = counts.get("delivered", 0) + counts.get("acknowledged", 0)
    attention = []
    if conflict or controller_conflict:
        attention.append(native_issue)
    if turn_state == "waiting_for_input":
        attention.append("waiting_for_input")
    if native_session and session.get("phase") == "error":
        attention.append("native_turn_error")
    if turn_state == "idle" and unfinished:
        attention.append("idle_with_unfinished_messages")
    if turn_state == "stop_observed" and unfinished:
        attention.append("stop_observed_with_unfinished_messages")
    unreceived = [s for s in submitted if observed - s["submitted_at"] >= 10]
    if unreceived:
        attention.append("submitted_without_receiver_confirmation")
        if turn_state == "idle":
            attention.append("idle_with_submitted_unreceived_messages")
    if turn_state == "approval_requested":
        attention.append("approval_requested")
    if unfinished and (progress_age is None or progress_age >= stale_after_seconds):
        attention.append("progress_needs_verification")
    next_action = (
        "Check the original message IDs and actual work; do not redispatch ACKed tasks."
        if attention
        else "Query original IDs for receiver results; idle does not mean task success."
    )
    if turn_state == "unknown":
        next_action = "Read the native observation reason and verify the app endpoint or exact workspace/session binding. Do not infer stopped from silence."
    return {
        "alias": binding["alias"],
        "harness": binding["harness"],
        "session_id": binding["session_id"],
        "binding_revision": binding["revision"],
        "observed_at": observed,
        "turn_state": turn_state,
        "turn_evidence": evidence,
        "native": native,
        "last_hook": event,
        "last_event_age_seconds": event_age,
        "last_progress_age_seconds": progress_age,
        "message_counts": counts,
        "latest_message": dict(latest) if latest else None,
        "unfinished_messages": pending,
        "submitted_unreceived_count": len(submitted),
        "submitted_unreceived_messages": submitted[:10],
        "attention": attention,
        "next_action": next_action,
        "background_jobs": "unknown",
        "limits": [
            "Stop is a pre-end observation; another Hook may continue the turn.",
            "Idle describes the main agent turn, not independent background processes.",
            "ACK and receiver terminal results do not independently verify task correctness.",
        ],
    }


def agent_status(
    box: Any,
    alias: str,
    *,
    observe_native: bool = True,
    stale_after_seconds: float = 300,
) -> dict:
    """Query the alias's current explicit binding, then ensure it did not change."""
    binding = box.binding(alias)
    result = inspect_target(
        box,
        binding,
        observe_native=observe_native,
        stale_after_seconds=stale_after_seconds,
    )
    if box.binding(alias) != binding:
        raise BridgeError("Binding changed during activity query; query again")
    return result


def with_activity(box: Any, message: dict, *, observe_native: bool = True) -> dict:
    """Decorate a receipt for its frozen recipient, never the alias's replacement."""
    binding = {
        "alias": message["alias"],
        "harness": message["harness"],
        "session_id": message["session_id"],
        "revision": message["binding_revision"],
        "title": "",
    }
    return {
        **message,
        "agent": inspect_target(box, binding, observe_native=observe_native),
    }


def wait_with_activity(
    box: Any, message_id: str, timeout: float = 30, until: str = "completed"
) -> dict:
    """Return real receipts or actionable idle/Stop gaps, without sending again.

    This is an active controller wait, not an unsolicited desktop notification.
    Native reads are spaced five seconds apart and require a 20-second budget;
    short waits use passive observations so the existing 55-second bound holds.
    """
    if (
        isinstance(timeout, bool)
        or not isinstance(timeout, (int, float))
        or not math.isfinite(timeout)
        or not 0 <= timeout <= 55
    ):
        raise BridgeError("timeout must be 0..55 finite seconds")
    if until not in ("acknowledged", "completed"):
        raise BridgeError("until must be acknowledged or completed")
    deadline = time.monotonic() + timeout
    wanted = {"completed", "failed"}
    if until == "acknowledged":
        wanted.add("acknowledged")
    next_probe = 0.0
    result = None
    while True:
        message = box.status(message_id)
        remaining = deadline - time.monotonic()
        if message["state"] in wanted:
            return {
                **with_activity(box, message, observe_native=remaining >= 20),
                "wait_reason": "receipt",
            }
        if result is None or time.monotonic() >= next_probe:
            result = with_activity(box, message, observe_native=remaining >= 20)
            next_probe = time.monotonic() + 5
            # A receiver may reply during observation; prefer that fresh receipt.
            message = box.status(message_id)
            if message["state"] in wanted:
                return {
                    **with_activity(
                        box, message, observe_native=deadline - time.monotonic() >= 20
                    ),
                    "wait_reason": "receipt",
                }
            agent = result["agent"]
            gap = message["state"] in ("delivered", "acknowledged") and (
                time.time()
                - (
                    message.get("acknowledged_at")
                    or message.get("delivered_at")
                    or message["created_at"]
                )
                >= 5
            )
            outbox = message.get("dispatch_queue") or {}
            unreceived = (
                message["state"] == "queued"
                and outbox.get("state") == "submitted"
                and time.time() - outbox["updated_at"] >= 10
            )
            if (
                (gap and agent["turn_state"] in ("idle", "stop_observed"))
                or (unreceived and agent["turn_state"] == "idle")
                or agent["turn_state"] in ("approval_requested", "waiting_for_input")
            ):
                return {**result, **message, "wait_reason": "attention_required"}
        if time.monotonic() >= deadline:
            return {**result, **message, "wait_reason": "timeout"}
        time.sleep(min(0.4, max(0, deadline - time.monotonic())))
