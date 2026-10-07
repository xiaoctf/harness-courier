"""Background Electron delivery through an explicitly enabled loopback DevTools port.

No OS mouse, keyboard, clipboard, foreground, or authentication APIs are used.
Normal delivery submits an opaque wake marker; explicit guarded recovery may
submit the original task text. Receiver Hooks retain ID checks.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from pathlib import Path
from urllib.parse import unquote, urlsplit
from urllib.request import ProxyHandler, build_opener

from harness_courier.app_registry import APPS, verified_port
from harness_courier.errors import DriverError

ROOT = Path(__file__).resolve().parent


def endpoint_owner(harness: str) -> int:
    """Require the shared, executable-verified loopback endpoint."""
    port = verified_port(harness, APPS)
    if port is None:
        raise DriverError(
            f"{harness} background endpoint unavailable; close the app and run this copy's start_background.cmd"
        )
    return port


def main_page(harness, target):
    if target.get("type") != "page":
        return False
    url = urlsplit(target.get("url", ""))
    if harness == "kimi":
        return (
            url.scheme == "app"
            and url.netloc == "renderer"
            and url.path != "/browser-overlay.html"
        )
    expected = (
        APPS["zcode"][0].parent
        / "resources"
        / "app.asar"
        / "out"
        / "renderer"
        / "index.html"
    )
    actual = unquote(url.path).lstrip("/")
    return url.scheme == "file" and os.path.normcase(
        actual.replace("/", "\\")
    ) == os.path.normcase(str(expected))


class CdpClient:
    def __init__(self, harness):
        self.harness = harness
        self.port = endpoint_owner(harness)
        self.socket = None
        self.sequence = 0
        self.opener = build_opener(ProxyHandler({}))

    def targets(self):
        with self.opener.open(
            f"http://127.0.0.1:{self.port}/json/list", timeout=3
        ) as response:
            data = json.loads(response.read(262144))
        if not isinstance(data, list):
            raise DriverError("Invalid DevTools target list")
        return [t for t in data if main_page(self.harness, t)]

    def attach(self, target):
        import websocket

        self.close()
        address = urlsplit(target.get("webSocketDebuggerUrl", ""))
        if (
            address.scheme != "ws"
            or address.hostname not in ("127.0.0.1", "localhost")
            or address.port != self.port
            or not address.path.startswith("/devtools/page/")
        ):
            raise DriverError("Rejected nonlocal or non-page DevTools address")
        self.socket = websocket.create_connection(
            target["webSocketDebuggerUrl"],
            timeout=5,
            suppress_origin=True,
            http_no_proxy=["127.0.0.1", "localhost"],
        )

    def call(self, method, params):
        # This adapter never admits browser activation, navigation, OS input, or arbitrary callers.
        if method not in (
            "Runtime.evaluate",
            "Input.insertText",
            "Input.dispatchKeyEvent",
        ):
            raise DriverError("CDP method outside delivery surface")
        if method == "Input.dispatchKeyEvent" and params not in (
            {
                "type": "keyDown",
                "key": "Enter",
                "code": "Enter",
                "windowsVirtualKeyCode": 13,
            },
            {
                "type": "keyUp",
                "key": "Enter",
                "code": "Enter",
                "windowsVirtualKeyCode": 13,
            },
        ):
            raise DriverError("Only the verified composer Enter submission is admitted")
        self.sequence += 1
        self.socket.send(
            json.dumps({"id": self.sequence, "method": method, "params": params})
        )
        deadline = time.monotonic() + 6
        while time.monotonic() < deadline:
            response = json.loads(self.socket.recv())
            if response.get("id") != self.sequence:
                continue
            if "error" in response:
                # Do not include arbitrary renderer output in errors or logs.
                raise DriverError("DevTools command rejected")
            return response.get("result", {})
        raise DriverError("DevTools outcome unknown; verify receipt before retrying")

    def evaluate(self, operation, binding, marker=""):
        args = json.dumps(
            {
                "op": operation,
                "harness": binding["harness"],
                "sid": binding["session_id"],
                "title": binding["title"],
                "marker": marker,
            }
        )
        expression = "(" + DOM_FUNCTION + ")(" + args + ")"
        result = self.call(
            "Runtime.evaluate", {"expression": expression, "returnByValue": True}
        )
        if result.get("exceptionDetails"):
            raise DriverError("Background renderer operation failed")
        value = result.get("result", {}).get("value")
        if not isinstance(value, dict):
            raise DriverError("Renderer did not return a bounded delivery observation")
        return value

    def close(self):
        if self.socket:
            self.socket.close()
            self.socket = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


# Read only composer state and routing metadata, never transcript text or credentials.
# Each mutation rechecks the real session ID and current composer immediately.
DOM_FUNCTION = (
    Path(__file__).parent / "harness_courier" / "composer_guard.js"
).read_text(encoding="utf-8")


def require(value, key):
    if value.get("error") or not value.get(key):
        # Routing diagnostics contain IDs/counts only, never draft or transcript text.
        details = {
            k: value.get(k)
            for k in ("sid", "matches", "editor_count", "draft_empty")
            if k in value
        }
        suffix = " | route=" + json.dumps(details, sort_keys=True) if details else ""
        raise DriverError(
            "Background delivery stopped: "
            + value.get("error", key + " not verified")
            + suffix
        )


def _attach_bound_session(client, binding, marker, *, allow_busy_navigation=False):
    """Choose exactly one bound renderer; route selection rechecks busy/draft guards."""
    targets = client.targets()
    observations = []
    for target in targets:
        client.attach(target)
        observations.append((target, client.evaluate("observe", binding, marker)))
    exact = [
        t for t, o in observations if o.get("matches") and o.get("editor_count") == 1
    ]
    if len(exact) == 1:
        client.attach(exact[0])
    elif len(exact) > 1:
        raise DriverError("Multiple desktop windows expose the bound session")
    elif len(targets) == 1:
        client.attach(targets[0])
        select_op = (
            "select_authorized_busy_navigation" if allow_busy_navigation else "select"
        )
        require(client.evaluate(select_op, binding, marker), "selected")
        # App route selection is asynchronous; preserve strict identity
        # checks while allowing a bounded cold composer mount.
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            observed = client.evaluate("observe", binding, marker)
            if observed.get("matches") and observed.get("editor_count") == 1:
                break
            time.sleep(0.15)
        require(observed, "matches")
    else:
        raise DriverError(
            "Cannot identify one bound desktop window; open the intended chat first"
        )


def _await_composer_ready(client, binding, marker):
    """Allow a bounded render delay before persisting the submission attempt."""
    deadline = time.monotonic() + 2
    while True:
        ready = client.evaluate("ready", binding, marker)
        if ready.get("ready") or time.monotonic() >= deadline:
            break
        time.sleep(0.1)
    require(ready, "ready")


def _submit_composer(client, binding, marker):
    """Use Kimi's exact composer Enter handler or ZCode's guarded send button."""
    if binding["harness"] == "kimi":
        # Use the composer's keyboard handler, not a programmatic
        # button click. The current Kimi UI accepted the exact marker
        # through Enter where click/clear did not prove that marker
        # reached the turn. Recheck route, draft, focus and send guard.
        require(client.evaluate("keyboard_submit_ready", binding, marker), "ready")
        for event_type in ("keyDown", "keyUp"):
            client.call(
                "Input.dispatchKeyEvent",
                {
                    "type": event_type,
                    "key": "Enter",
                    "code": "Enter",
                    "windowsVirtualKeyCode": 13,
                },
            )
    else:
        require(client.evaluate("submit", binding, marker), "submitted")


def _confirm_composer_clear(client, binding, marker):
    """Require the same session and an empty composer; this is still not an ACK."""
    deadline = time.monotonic() + 3
    while True:
        observed = client.evaluate("observe", binding, marker)
        if observed.get("matches") and observed.get("draft_empty") is True:
            break
        if time.monotonic() >= deadline:
            raise DriverError("Submission not confirmed by composer; outcome uncertain")
        time.sleep(0.1)


def deliver_background(
    binding,
    message_id,
    client_factory=CdpClient,
    *,
    recovery_sha=None,
    recovery_status=None,
    recovery_body=None,
    resume_staged=False,
    allow_busy_navigation=False,
):
    if binding["harness"] not in APPS or not re.fullmatch(
        r"msg_[a-f0-9]{32}", message_id
    ):
        raise DriverError("Invalid background recipient or message ID")
    marker = f"[HARNESS_BRIDGE_WAKE:{message_id}]"
    if recovery_body is not None:
        if (
            recovery_sha is None
            or not isinstance(recovery_body, str)
            or not recovery_body.strip()
            or len(recovery_body) > 40000
            or recovery_body.lstrip().startswith("[HARNESS_BRIDGE_WAKE:")
        ):
            raise DriverError(
                "Task-body recovery requires explicit unreceived recovery and ordinary text"
            )
        marker = recovery_body
    # Serialize bridge transports; never activate a foreground input fallback.
    from desktop_delivery import desktop_lock

    with desktop_lock():
        journal = ROOT / "data" / "background-delivery" / (message_id + ".json")
        recovering = recovery_sha is not None
        original_sha = None
        staged = False

        def check_unreceived():
            if not callable(recovery_status):
                raise DriverError("Recovery requires a fresh mailbox status guard")
            state = recovery_status()
            if (
                state.get("id") != message_id
                or state.get("state") != "queued"
                or state.get("delivered_at") is not None
                or state.get("acknowledged_at") is not None
                or state.get("session_id") != binding["session_id"]
                or state.get("harness") != binding["harness"]
                or state.get("binding_revision") != binding.get("revision")
            ):
                raise DriverError(
                    "Recovery refused: message received or binding changed"
                )

        if recovering:
            if not re.fullmatch(r"[a-f0-9]{64}", recovery_sha) or not journal.exists():
                raise DriverError(
                    "Recovery requires an existing journal and exact SHA256"
                )
            original_sha = hashlib.sha256(journal.read_bytes()).hexdigest()
            if original_sha != recovery_sha:
                raise DriverError("Recovery journal SHA mismatch")
            check_unreceived()
        if journal.exists():
            saved = json.loads(journal.read_text(encoding="utf-8"))
            if (
                saved.get("sid") != binding["session_id"]
                or saved.get("harness") != binding["harness"]
            ):
                raise DriverError("Background journal target mismatch")
            if saved.get("phase") == "submitted" and not recovering:
                return {
                    "submitted": True,
                    "transport": "cdp_background",
                    "receiver_verified": False,
                    "duplicate_prevented": True,
                }
            if saved.get("phase") != "submitted":
                raise DriverError(
                    "Prior background submission outcome uncertain; check receiver receipt; no automatic resend"
                )
        if recovering:
            # Preserve the original record. One explicit recovery only; crashes
            # and lost responses retain a guard and never auto-retry.
            journal = journal.with_name(message_id + ".recovery-1.json")
            if journal.exists():
                saved_recovery = json.loads(journal.read_text(encoding="utf-8"))
                if (
                    not resume_staged
                    or recovery_body is None
                    or saved_recovery.get("phase") != "recovery_reserved"
                    or saved_recovery.get("sid") != binding["session_id"]
                    or saved_recovery.get("harness") != binding["harness"]
                    or saved_recovery.get("original_journal_sha256") != original_sha
                ):
                    raise DriverError(
                        "Recovery already attempted; inspect receipt, no automatic resend"
                    )
                staged = True
        with client_factory(binding["harness"]) as client:
            _attach_bound_session(
                client, binding, marker, allow_busy_navigation=allow_busy_navigation
            )
            # Normal delivery sends only a wake; explicit recovery may use task text.
            if staged:
                # No new typing or draft mutation: only the exact, unsent
                # task body from a pre-submit reserved journal can continue.
                require(
                    client.evaluate("keyboard_submit_ready", binding, marker), "ready"
                )
            else:
                require(client.evaluate("focus", binding, marker), "focused")
            if recovering and not staged:
                check_unreceived()
                record = {
                    "message_id": message_id,
                    "harness": binding["harness"],
                    "sid": binding["session_id"],
                    "phase": "recovery_reserved",
                    "original_journal_sha256": original_sha,
                    "at": time.time(),
                }
                with journal.open("x", encoding="utf-8") as f:
                    json.dump(record, f)
                    f.flush()
                    os.fsync(f.fileno())
            if not staged:
                client.call("Input.insertText", {"text": marker})
            _await_composer_ready(client, binding, marker)
            journal.parent.mkdir(parents=True, exist_ok=True)
            saved = {
                "message_id": message_id,
                "harness": binding["harness"],
                "sid": binding["session_id"],
                "phase": "submit_attempt",
                "at": time.time(),
            }
            if recovering:
                saved["original_journal_sha256"] = original_sha
            journal.write_text(json.dumps(saved), encoding="utf-8")
            # A lost response after this point must never cause an automatic resubmit.
            _submit_composer(client, binding, marker)
            # A click is not application acceptance. Require the verified
            # composer to clear, while still leaving receipt proof to the Hook.
            _confirm_composer_clear(client, binding, marker)
            saved["phase"] = "submitted"
            temporary = journal.with_suffix(".tmp")
            temporary.write_text(json.dumps(saved), encoding="utf-8")
            os.replace(temporary, journal)
            return {
                "submitted": True,
                "transport": "cdp_background",
                "receiver_verified": False,
                "delivery_state": "SUBMITTED_UNCONFIRMED",
                "payload_mode": "same_id_task_body"
                if recovery_body is not None
                else "wake_marker",
                "composer_cleared": True,
                "recovery": recovering,
                "note": "Receiver Hook and agent ACK/result prove actual receipt",
            }
