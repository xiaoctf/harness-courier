"""SQLite mailbox: exact-session bindings, message state and receiver receipts.

Desktop submission is a separate transport event. Only the receiver Hook may
mark a message delivered; ACK and terminal results belong to that same session.
"""

from __future__ import annotations

import contextlib
import re
import sqlite3
import time
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from .errors import BridgeError
from .paths import DEFAULT_DB, HARNESS


def valid_id(value: Any, label: str = "session_id") -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,160}", value):
        raise BridgeError(f"Invalid {label}")
    return value


def now() -> float:
    return time.time()


class Mailbox:
    """Persist messages and enforce recipient identity inside SQLite transactions.

    Each operation opens its own connection. BEGIN IMMEDIATE protects binding,
    delivery and receipt transitions from concurrent Hook/MCP processes.
    """

    def __init__(self, path: str | Path = DEFAULT_DB) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS peers (
                  harness TEXT NOT NULL, session_id TEXT NOT NULL,
                  title TEXT NOT NULL, cwd TEXT NOT NULL, seen_at REAL NOT NULL,
                  guard_ready INTEGER NOT NULL DEFAULT 0, online INTEGER NOT NULL DEFAULT 1,
                  PRIMARY KEY(harness, session_id));
                CREATE TABLE IF NOT EXISTS bindings (
                  alias TEXT PRIMARY KEY, harness TEXT NOT NULL, session_id TEXT NOT NULL,
                  title TEXT NOT NULL, revision INTEGER NOT NULL, bound_at REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS messages (
                  id TEXT PRIMARY KEY, alias TEXT NOT NULL, binding_revision INTEGER NOT NULL,
                  harness TEXT NOT NULL, session_id TEXT NOT NULL,
                  sender_harness TEXT NOT NULL, sender_session_id TEXT NOT NULL,
                  body TEXT NOT NULL, state TEXT NOT NULL, created_at REAL NOT NULL,
                  delivered_at REAL, acknowledged_at REAL, completed_at REAL,
                  result TEXT, result_kind TEXT, dispatch_error TEXT);
                CREATE INDEX IF NOT EXISTS pending_session ON messages(harness, session_id, state);
            """)

    @contextlib.contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        """Commit a successful operation; roll back errors and always close."""
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA journal_mode=WAL")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def register(
        self,
        harness: str,
        session_id: str,
        title: str,
        cwd: str,
        guard_ready: bool = False,
        online: bool = True,
    ) -> dict[str, Any]:
        """Record the actual Hook session while preserving an installed guard."""
        if harness not in HARNESS:
            raise BridgeError("Unknown harness")
        valid_id(session_id)
        if (
            not isinstance(title, str)
            or len(title) > 500
            or not isinstance(cwd, str)
            or len(cwd) > 1000
        ):
            raise BridgeError("Invalid peer metadata")
        with self.connect() as db:
            previous = db.execute(
                "SELECT guard_ready FROM peers WHERE harness=? AND session_id=?",
                (harness, session_id),
            ).fetchone()
            ready = bool(guard_ready or (previous and previous["guard_ready"]))
            db.execute(
                """INSERT INTO peers VALUES(?,?,?,?,?,?,?) ON CONFLICT(harness,session_id)
                       DO UPDATE SET title=excluded.title,cwd=excluded.cwd,seen_at=excluded.seen_at,
                       guard_ready=excluded.guard_ready,online=excluded.online""",
                (harness, session_id, title, cwd, now(), int(ready), int(online)),
            )
        return {"harness": harness, "session_id": session_id, "guard_ready": ready}

    def peers(self) -> dict[str, Any]:
        """List recent sessions and every persistent alias binding."""
        with self.connect() as db:
            return {
                "peers": [
                    dict(r)
                    for r in db.execute(
                        "SELECT * FROM peers ORDER BY seen_at DESC LIMIT 100"
                    )
                ],
                "bindings": [
                    dict(r) for r in db.execute("SELECT * FROM bindings ORDER BY alias")
                ],
            }

    def bind(
        self,
        alias: str,
        harness: str,
        session_id: str,
        title: str | None = None,
        replace: bool = False,
    ) -> dict[str, Any]:
        """Bind a guarded online session; changing a target requires replace=True."""
        valid_id(alias, "alias")
        valid_id(session_id)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            peer = db.execute(
                "SELECT * FROM peers WHERE harness=? AND session_id=?",
                (harness, session_id),
            ).fetchone()
            if not peer or not peer["guard_ready"]:
                raise BridgeError(
                    "Target must register through its actual session Hook before binding"
                )
            if not peer["online"]:
                raise BridgeError("Target session is offline")
            previous = db.execute(
                "SELECT * FROM bindings WHERE alias=?", (alias,)
            ).fetchone()
            same = previous and (previous["harness"], previous["session_id"]) == (
                harness,
                session_id,
            )
            if previous and not same and not replace:
                raise BridgeError(
                    "Alias already points to a different session; explicit replace is required"
                )
            revision = (
                previous["revision"]
                if same
                else (previous["revision"] + 1 if previous else 1)
            )
            target_title = title if title is not None else peer["title"]
            if not target_title or len(target_title) > 200:
                raise BridgeError("Provide the exact visible chat title")
            db.execute(
                """INSERT INTO bindings VALUES(?,?,?,?,?,?) ON CONFLICT(alias) DO UPDATE SET
                       harness=excluded.harness,session_id=excluded.session_id,title=excluded.title,
                       revision=excluded.revision,bound_at=excluded.bound_at""",
                (alias, harness, session_id, target_title, revision, now()),
            )
            return dict(
                db.execute("SELECT * FROM bindings WHERE alias=?", (alias,)).fetchone()
            )

    def binding(self, alias: str) -> dict[str, Any]:
        """Read the current target of an alias."""
        with self.connect() as db:
            row = db.execute(
                "SELECT * FROM bindings WHERE alias=?", (alias,)
            ).fetchone()
        if not row:
            raise BridgeError("Alias is not bound")
        return dict(row)

    def send(
        self,
        alias: str,
        body: str,
        sender_harness: str = "codex",
        sender_session_id: str = "controller",
        dispatch: bool = False,
        *,
        allow_offline: bool = False,
    ) -> dict[str, Any]:
        """Snapshot the binding and queue text; optionally submit a desktop wake."""
        valid_id(sender_session_id, "sender_session_id")
        if (
            sender_harness not in HARNESS
            or not isinstance(body, str)
            or not body.strip()
            or len(body) > 6000
        ):
            raise BridgeError(
                "Message must be nonempty text of at most 6000 characters"
            )
        message_id = "msg_" + uuid.uuid4().hex
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            target = db.execute(
                "SELECT * FROM bindings WHERE alias=?", (alias,)
            ).fetchone()
            if not target:
                raise BridgeError("Alias is not bound; no message was sent")
            peer = db.execute(
                "SELECT * FROM peers WHERE harness=? AND session_id=?",
                (target["harness"], target["session_id"]),
            ).fetchone()
            if (
                not peer
                or not peer["guard_ready"]
                or (not peer["online"] and not allow_offline)
            ):
                raise BridgeError(
                    "Bound session is offline or lacks the receiver guard"
                )
            db.execute(
                """INSERT INTO messages(id,alias,binding_revision,harness,session_id,sender_harness,
                       sender_session_id,body,state,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (
                    message_id,
                    alias,
                    target["revision"],
                    target["harness"],
                    target["session_id"],
                    sender_harness,
                    sender_session_id,
                    body,
                    "queued",
                    now(),
                ),
            )
        if dispatch:
            return self.dispatch(message_id)
        return self.status(message_id)

    def status(self, message_id: str) -> dict[str, Any]:
        """Read persisted state and describe which receipt is still missing."""
        valid_id(message_id, "message_id")
        with self.connect() as db:
            row = db.execute(
                "SELECT * FROM messages WHERE id=?", (message_id,)
            ).fetchone()
        if not row:
            raise BridgeError("Unknown message_id")
        result = dict(row)
        result["waiting_for"] = {
            "queued": "desktop delivery or next target turn",
            "delivered": "agent ACK",
            "acknowledged": "agent result",
            "completed": None,
            "failed": None,
        }[row["state"]]
        from dispatch_queue import queue_status

        dispatch_queue = queue_status(self, message_id)
        if dispatch_queue is not None:
            result["dispatch_queue"] = dispatch_queue
        return result

    def receive(
        self,
        harness: str,
        session_id: str,
        message_id: str | None = None,
        limit: int = 10,
        *,
        include_acknowledged: bool = True,
    ) -> list[dict[str, Any]]:
        """Fetch only this session's messages and mark queued items delivered."""
        valid_id(session_id)
        if not isinstance(limit, int) or not 1 <= limit <= 10:
            raise BridgeError("Invalid receive limit")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if message_id:
                row = db.execute(
                    "SELECT * FROM messages WHERE id=?", (message_id,)
                ).fetchone()
                if not row or (row["harness"], row["session_id"]) != (
                    harness,
                    session_id,
                ):
                    raise BridgeError(
                        "SESSION_MISMATCH: message is addressed to a different chat"
                    )
                rows = (
                    [row]
                    if row["state"] in ("queued", "delivered", "acknowledged")
                    else []
                )
            else:
                states = (
                    "('queued','delivered','acknowledged')"
                    if include_acknowledged
                    else "('queued','delivered')"
                )
                rows = db.execute(
                    """SELECT * FROM messages WHERE harness=? AND session_id=?
                                  AND state IN """
                    + states
                    + """
                                  ORDER BY CASE WHEN state='acknowledged' THEN 1 ELSE 0 END,
                                  created_at, id LIMIT ?""",
                    (harness, session_id, limit),
                ).fetchall()
            for row in rows:
                db.execute(
                    """UPDATE messages SET state=CASE WHEN state='queued' THEN 'delivered' ELSE state END,
                           delivered_at=COALESCE(delivered_at,?) WHERE id=?""",
                    (now(), row["id"]),
                )
            return [
                {
                    **dict(row),
                    "state": "delivered" if row["state"] == "queued" else row["state"],
                }
                for row in rows
            ]

    def acknowledge(
        self, harness: str, session_id: str, message_id: str
    ) -> dict[str, Any]:
        """Record receipt for a message already fetched by this exact session."""
        return self._receipt(harness, session_id, message_id, None, None)

    def reply(
        self,
        harness: str,
        session_id: str,
        message_id: str,
        body: str,
        result_kind: str = "completed",
    ) -> dict[str, Any]:
        """Store a terminal result, rejecting conflicting duplicate receipts."""
        if (
            result_kind not in ("completed", "failed")
            or not isinstance(body, str)
            or not body.strip()
            or len(body) > 20000
        ):
            raise BridgeError("Invalid result")
        return self._receipt(harness, session_id, message_id, body, result_kind)

    def _receipt(
        self,
        harness: str,
        session_id: str,
        message_id: str,
        body: str | None,
        result_kind: str | None,
    ) -> dict[str, Any]:
        """Apply ACK/result atomically after verifying recipient and state."""
        valid_id(session_id)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT * FROM messages WHERE id=?", (message_id,)
            ).fetchone()
            if not row or (row["harness"], row["session_id"]) != (harness, session_id):
                raise BridgeError("SESSION_MISMATCH: receipt rejected")
            if row["state"] == "queued":
                raise BridgeError(
                    "Receiver must fetch its message before sending a receipt"
                )
            if row["state"] in ("completed", "failed"):
                if body is not None and (
                    row["result"] != body or row["result_kind"] != result_kind
                ):
                    raise BridgeError("Conflicting terminal receipt")
                return dict(row)
            if body is None:
                db.execute(
                    "UPDATE messages SET state='acknowledged',acknowledged_at=COALESCE(acknowledged_at,?) WHERE id=?",
                    (now(), message_id),
                )
            else:
                db.execute(
                    """UPDATE messages SET state=?,acknowledged_at=COALESCE(acknowledged_at,?),
                           completed_at=?,result=?,result_kind=? WHERE id=?""",
                    (result_kind, now(), now(), body, result_kind, message_id),
                )
        return self.status(message_id)

    def dispatch(
        self,
        message_id: str,
        *,
        allow_busy_navigation: bool = False,
        priority: bool = False,
    ) -> dict[str, Any]:
        """Attempt a wake without promoting mailbox delivery or ACK state."""
        if type(allow_busy_navigation) is not bool or type(priority) is not bool:
            raise BridgeError("Delivery options must be booleans")
        from priority_policy import resolve_priority

        priority = resolve_priority(self.path, priority)
        message = self.status(message_id)
        # SessionStart may deliver Hook context before the composer loads.
        # An explicit retry can still wake that chat if GUI delivery failed;
        # acknowledged/terminal messages must never be dispatched again.
        if message["state"] != "queued" and not (
            message["state"] == "delivered" and message["dispatch_error"]
        ):
            return message
        target = self.binding(message["alias"])
        if (target["harness"], target["session_id"], target["revision"]) != (
            message["harness"],
            message["session_id"],
            message["binding_revision"],
        ):
            raise BridgeError(
                "Binding changed; queued message retains its original target"
            )

        def guard():
            fresh = self.status(message_id)
            current_binding = self.binding(message["alias"])
            if (
                current_binding["harness"],
                current_binding["session_id"],
                current_binding["revision"],
            ) != (
                message["harness"],
                message["session_id"],
                message["binding_revision"],
            ):
                raise BridgeError("Binding changed while waiting for dispatch")
            return fresh["state"] in ("queued", "delivered")

        try:
            if allow_busy_navigation or priority:
                from cdp_transport import deliver_background

                delivery = deliver_background(
                    target,
                    message_id,
                    allow_busy_navigation=allow_busy_navigation,
                    priority=priority,
                    dispatch_guard=guard,
                )
            else:
                from desktop_delivery import deliver_wake

                delivery = deliver_wake(target, message_id, dispatch_guard=guard)
            error = None
        except Exception as exc:
            delivery = {
                "submitted": False,
                "reason": str(exc),
                "retry_safe": getattr(exc, "retry_safe", False),
            }
            error = str(exc)[:1000]
        with self.connect() as db:
            db.execute(
                "UPDATE messages SET dispatch_error=? WHERE id=?", (error, message_id)
            )
        # GUI submission never counts as delivered or acknowledged: the receiver Hook does that.
        return {**self.status(message_id), "desktop_delivery": delivery}

    def wait(
        self, message_id: str, timeout: float = 30, until: str = "completed"
    ) -> dict[str, Any]:
        """Poll receipts for at most 55 seconds; timeout returns current state."""
        if not isinstance(timeout, (int, float)) or not 0 <= timeout <= 55:
            raise BridgeError("timeout must be 0..55 seconds")
        if until not in ("acknowledged", "completed"):
            raise BridgeError("until must be acknowledged or completed")
        deadline = time.monotonic() + timeout
        wanted = {"completed", "failed"} | (
            {"acknowledged"} if until == "acknowledged" else set()
        )
        while True:
            state = self.status(message_id)
            if state["state"] in wanted or time.monotonic() >= deadline:
                return state
            time.sleep(min(0.4, max(0, deadline - time.monotonic())))
