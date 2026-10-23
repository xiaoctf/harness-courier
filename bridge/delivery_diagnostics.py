"""Bounded transport diagnostics and read-only reconnection before input."""

from __future__ import annotations

import re
import time

from cu_client import DriverError
from websocket import WebSocketConnectionClosedException, WebSocketTimeoutException

DISCONNECT_ERRORS = (
    WebSocketConnectionClosedException,
    ConnectionResetError,
    ConnectionAbortedError,
    BrokenPipeError,
)
READ_TRANSPORT_ERRORS = (WebSocketTimeoutException, *DISCONNECT_ERRORS)


def failure_details(exc):
    """Expose fixed operation metadata, never renderer expressions or payloads."""
    result = {
        "submitted": False,
        "reason": str(exc),
        "retry_safe": getattr(exc, "retry_safe", False),
    }
    for key in ("error_code", "failure_stage", "operation"):
        value = getattr(exc, key, None)
        if isinstance(value, str) and re.fullmatch(r"[a-zA-Z_.]{1,80}", value):
            result[key] = value
    for key in ("elapsed_ms", "read_reconnects"):
        value = getattr(exc, key, None)
        if type(value) is int and value >= 0:
            result[key] = value
    return result


class DeliveryTrace:
    """One attempt, one read reconnect budget; mutations are never repeated."""

    def __init__(self, client):
        self.client = client
        self.stage = "renderer_list"
        self.target = None
        self.read_reconnects = 0
        self.input_started = False

    def _run(self, operation, function, *args):
        start = time.perf_counter()
        try:
            return function(*args)
        except Exception as exc:
            exc.failure_stage = self.stage
            exc.operation = operation
            exc.elapsed_ms = round((time.perf_counter() - start) * 1000)
            exc.read_reconnects = self.read_reconnects
            if isinstance(exc, WebSocketTimeoutException):
                exc.error_code = "transport_timeout"
            elif isinstance(exc, DISCONNECT_ERRORS):
                exc.error_code = "transport_disconnected"
            raise

    def targets(self):
        return self._run("renderer_list", self.client.targets)

    def attach(self, target):
        self.target = target
        return self._run("websocket_connect", self.client.attach, target)

    def evaluate(self, operation, binding, marker=""):
        return self._run(operation, self.client.evaluate, operation, binding, marker)

    def call(self, method, params):
        if method in ("Input.insertText", "Input.dispatchKeyEvent"):
            self.input_started = True
        return self._run(method, self.client.call, method, params)

    def observe_before_input(self, binding, marker, *, stage, target=None):
        if self.input_started:
            raise DriverError("Read reconnect refused after input")
        self.stage = stage
        try:
            if target is not None:
                self.attach(target)
            return self.evaluate("observe", binding, marker)
        except READ_TRANSPORT_ERRORS:
            if self.read_reconnects >= 1:
                raise
            self.read_reconnects += 1
            original_id = (self.target or {}).get("id")
            if not isinstance(original_id, str) or not original_id:
                raise DriverError(
                    "Read-only reconnect could not verify original renderer"
                ) from None
            refreshed = [t for t in self.targets() if t.get("id") == original_id]
            if len(refreshed) != 1:
                raise DriverError(
                    "Read-only reconnect could not verify original renderer"
                ) from None
            self.attach(refreshed[0])
            # Only this fixed read repeats. In particular, never select, focus,
            # type, submit, stop a turn, or promote a queued item again here.
            return self.evaluate("observe", binding, marker)
