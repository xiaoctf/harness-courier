"""Persistent, opt-in outbox with a single Windows delivery worker per mailbox.

Only explicit dispatch requests enter this table. Receiver states remain owned
by the mailbox/Hook; scheduler states never fabricate delivery, ACK or results.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

from cu_client import DriverError
from priority_policy import resolve_priority

ROOT = Path(__file__).resolve().parent
MAX_ATTEMPTS = 6
MAX_AGE = 600
BACKOFF = (2, 5, 15, 30, 60)
JOB_FIELDS = "state,priority,allow_busy_navigation,attempts,next_attempt_at,last_error,updated_at,delivery"


def initialize(box):
    with box.connect() as db:
        db.execute("""CREATE TABLE IF NOT EXISTS dispatch_jobs (
            message_id TEXT PRIMARY KEY, state TEXT NOT NULL,
            priority INTEGER NOT NULL, allow_busy_navigation INTEGER NOT NULL,
            attempts INTEGER NOT NULL DEFAULT 0, created_at REAL NOT NULL,
            updated_at REAL NOT NULL, next_attempt_at REAL NOT NULL,
            last_error TEXT, delivery TEXT)""")


def queue_status(box, message_id):
    """Read existing scheduler metadata without creating tables or jobs."""
    with box.connect() as db:
        exists = db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='dispatch_jobs'"
        ).fetchone()
        if not exists:
            return None
        row = db.execute(
            f"SELECT {JOB_FIELDS} FROM dispatch_jobs WHERE message_id=?", (message_id,)
        ).fetchone()
    if not row:
        return None
    result = dict(row)
    delivery = json.loads(result.pop("delivery") or "{}")
    result["last_delivery"] = {
        key: delivery[key]
        for key in (
            "submitted",
            "priority_action",
            "duplicate_prevented",
            "retry_safe",
            "reason",
        )
        if key in delivery
    }
    return result


def enqueue(box, message_id, *, priority=True, allow_busy_navigation=True, start=True):
    if not isinstance(message_id, str) or not re.fullmatch(
        r"msg_[a-f0-9]{32}", message_id
    ):
        raise ValueError("Invalid dispatch message ID")
    if type(priority) is not bool or type(allow_busy_navigation) is not bool:
        raise ValueError("Delivery options must be booleans")
    requested_priority = priority
    priority = resolve_priority(box.path, priority)
    message = box.status(message_id)
    if message["state"] in ("acknowledged", "completed", "failed") or (
        message["state"] == "delivered" and not message["dispatch_error"]
    ):
        return message
    target = box.binding(message["alias"])
    if (target["harness"], target["session_id"], target["revision"]) != (
        message["harness"],
        message["session_id"],
        message["binding_revision"],
    ):
        raise ValueError("Binding changed; queue will not retarget the message")
    initialize(box)
    stamp = time.time()
    with box.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        # Idempotent by message ID, including held/working/submitted jobs.
        # Explicit same-ID retry may resume a known pre-input hold after the user
        # fixes a draft/interface. Never clear uncertain submission protection.
        db.execute(
            """INSERT INTO dispatch_jobs
            (message_id,state,priority,allow_busy_navigation,created_at,updated_at,next_attempt_at)
            VALUES(?,?,?,?,?,?,?) ON CONFLICT(message_id) DO NOTHING""",
            (
                message_id,
                "pending",
                int(priority),
                int(allow_busy_navigation),
                stamp,
                stamp,
                stamp,
            ),
        )
        if resolve_priority(box.path, False):
            db.execute(
                "UPDATE dispatch_jobs SET priority=1 WHERE message_id=? AND state='pending'",
                (message_id,),
            )
    job = queue_status(box, message_id)
    journal = ROOT / "data" / "background-delivery" / (message_id + ".json")
    if (
        job["state"] in ("held", "exhausted")
        and job["last_delivery"].get("retry_safe") is True
        and not journal.exists()
    ):
        with box.connect() as db:
            db.execute(
                """UPDATE dispatch_jobs SET state='pending',attempts=0,created_at=?,
                next_attempt_at=?,updated_at=?,last_error=NULL
                WHERE message_id=? AND state IN ('held','exhausted')""",
                (stamp, stamp, stamp, message_id),
            )
    worker_pid = ensure_worker(box) if start else None
    result = box.status(message_id)
    result["dispatch_queue"] = queue_status(box, message_id)
    result["dispatch_queue"]["worker_start_requested"] = worker_pid is not None
    result["dispatch_queue"]["worker_pid"] = worker_pid
    result["desktop_delivery"] = {
        "submitted": None,
        "transport": "persistent_outbox",
        "requested_priority": requested_priority,
        "effective_priority": priority,
        "note": (
            "Dispatch queued; query the original ID for worker progress and receiver receipts"
            if result["dispatch_queue"]["state"] in ("pending", "working")
            else "Existing dispatch retained; inspect dispatch_queue state and original receipt"
        ),
    }
    return result


def ensure_worker(box):
    """Start a fixed child only for pending work; no host configuration changes."""
    initialize(box)
    with box.connect() as db:
        pending = db.execute(
            "SELECT 1 FROM dispatch_jobs WHERE state IN ('pending','working') LIMIT 1"
        ).fetchone()
    if not pending:
        return None
    try:
        process = subprocess.Popen(
            [
                sys.executable,
                str(ROOT / "dispatch_worker.py"),
                "--db",
                str(box.path.resolve()),
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            shell=False,
            close_fds=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return process.pid
    except OSError:
        # Durable pending records remain available on the next MCP startup/dispatch.
        return None


@contextlib.contextmanager
def worker_lock(box):
    """OS lock lifetime is the worker lifetime, released even after a crash."""
    if os.name != "nt":
        raise DriverError("Dispatch worker requires native Windows")
    import msvcrt

    with box.path.with_suffix(box.path.suffix + ".worker.lock").open("a+b") as handle:
        handle.seek(0)
        deadline = time.monotonic() + 3
        while True:
            handle.seek(0)
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    yield False
                    return
                time.sleep(0.05)
        try:
            yield True
        finally:
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)


def _retryable(delivery):
    # An exception before typing is necessary but not sufficient for retry.
    # Drafts, attachments, ambiguous identities, journals and changed bindings hold.
    if delivery.get("retry_safe") is not True:
        return False
    reason = delivery.get("reason", "")
    return any(
        token in reason
        for token in (
            "background endpoint unavailable",
            "Connection timed out",
            "timed out",
            "Desktop delivery wait timed out",
            "composer_unavailable_or_ambiguous",
            "current_conversation_generating",
        )
    ) and not any(
        token in reason.lower()
        for token in (
            "uncertain",
            "journal",
            "draft",
            "attachment",
            "binding",
            "owner",
            "identity",
        )
    )


def recover_working(box):
    """A crashed attempt is held; pending jobs survive without speculative replay."""
    with box.connect() as db:
        db.execute(
            """UPDATE dispatch_jobs SET state='held',updated_at=?,
            last_error='Worker interrupted; inspect original receipt/journal before retry'
            WHERE state='working'""",
            (time.time(),),
        )


def run_one(box, *, deliver=None, stamp=None):
    """Claim one eligible job atomically; invoked only by the exclusive worker."""
    stamp = time.time() if stamp is None else stamp
    with box.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        if resolve_priority(box.path, False):
            db.execute(
                "UPDATE dispatch_jobs SET priority=1 WHERE state='pending' AND priority=0"
            )
        job = db.execute(
            """SELECT * FROM dispatch_jobs WHERE state='pending'
            AND next_attempt_at<=? ORDER BY priority DESC,created_at,message_id LIMIT 1""",
            (stamp,),
        ).fetchone()
        if not job:
            return False
        job = dict(job)
        db.execute(
            "UPDATE dispatch_jobs SET state='working',attempts=attempts+1,updated_at=? WHERE message_id=?",
            (stamp, job["message_id"]),
        )
    mid = job["message_id"]
    if stamp - job["created_at"] > MAX_AGE:
        state, error, delivery = (
            "exhausted",
            "Dispatch retry window expired",
            json.loads(job["delivery"] or '{"retry_safe":true}'),
        )
    else:
        try:
            current = box.status(mid)
            if current["state"] in ("acknowledged", "completed", "failed"):
                result = current
            else:
                result = (deliver or box.dispatch)(
                    mid,
                    priority=bool(job["priority"]),
                    allow_busy_navigation=bool(job["allow_busy_navigation"]),
                )
            delivery = result.get("desktop_delivery", {})
            error = result.get("dispatch_error")
            if result["state"] in ("acknowledged", "completed", "failed"):
                state, error = "cancelled", None
            elif delivery.get("submitted") is True:
                state, error = "submitted", None
            elif _retryable(delivery):
                state = "pending" if job["attempts"] + 1 < MAX_ATTEMPTS else "exhausted"
            else:
                state = "held"
                error = (
                    error
                    or delivery.get("reason")
                    or "Submission not confirmed; inspect original receipt"
                )
        except ValueError as exc:
            # Mailbox validation happens before transport; retain its bounded
            # domain error, but require inspection instead of automatic replay.
            state, error, delivery = "held", str(exc), {}
        except Exception:
            # Unexpected failures are never eligible for automatic replay.
            state, error, delivery = (
                "held",
                "Unexpected worker failure; inspect original receipt/journal",
                {},
            )
    next_attempt = stamp + BACKOFF[min(job["attempts"], len(BACKOFF) - 1)]
    with box.connect() as db:
        db.execute(
            """UPDATE dispatch_jobs SET state=?,updated_at=?,next_attempt_at=?,last_error=?,delivery=?
            WHERE message_id=? AND state='working'""",
            (
                state,
                time.time(),
                next_attempt,
                error[:1000] if error else None,
                json.dumps(delivery),
                mid,
            ),
        )
    return True


def run_worker(box, *, idle_seconds=2, deliver=None):
    initialize(box)
    with worker_lock(box) as owned:
        if not owned:
            return
        recover_working(box)
        idle_since = time.monotonic()
        deadline = idle_since + MAX_AGE + 60
        while time.monotonic() < deadline:
            if run_one(box, deliver=deliver):
                idle_since = time.monotonic()
                continue
            with box.connect() as db:
                pending = db.execute(
                    "SELECT min(next_attempt_at) FROM dispatch_jobs WHERE state='pending'"
                ).fetchone()[0]
            if pending is None:
                if time.monotonic() - idle_since >= idle_seconds:
                    return
                time.sleep(0.1)
            else:
                time.sleep(min(0.5, max(0.05, pending - time.time())))
    # Handoff only outstanding explicit jobs if this finite worker reaches its cap.
    ensure_worker(box)
