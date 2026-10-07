"""Line-delimited stdio MCP adapter. stdout carries protocol messages only."""

from __future__ import annotations

import json
import sys
from functools import partial
from typing import Any, TextIO

from dispatch_runner import dispatch_fresh

from .errors import BridgeError
from .mailbox import Mailbox
from .mcp_tools import invoke, tool_specs

PROTOCOL_VERSION = "2024-11-05"
SERVER_INFO = {"name": "harness-courier", "version": "0.1.0"}


def error_response(request_id: Any, code: int, message: str) -> dict:
    """Build one JSON-RPC error without mixing protocol and tool errors."""
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": code, "message": message},
    }


def call_tool(box: Mailbox, harness: str, params: dict) -> dict:
    """Represent domain failures as MCP tool results, keeping the stream alive."""
    try:
        value = invoke(
            box,
            harness,
            params.get("name"),
            params.get("arguments", {}),
            dispatch_handler=partial(dispatch_fresh, box),
        )
        text = json.dumps(value, ensure_ascii=False)
        failed = False
    except (BridgeError, TypeError, ValueError, OSError) as exc:
        text, failed = str(exc), True
    return {"content": [{"type": "text", "text": text}], "isError": failed}


def handle_request(box: Mailbox, harness: str, request: Any) -> dict | None:
    """Handle one request; notifications intentionally have no response."""
    if not isinstance(request, dict):
        raise BridgeError("Request must be an object")
    if "id" not in request:
        return None
    method = request.get("method")
    if method == "initialize":
        result = {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": dict(SERVER_INFO),
        }
    elif method == "ping":
        result = {}
    elif method == "tools/list":
        result = {"tools": tool_specs(harness)}
    elif method == "tools/call":
        result = call_tool(box, harness, request.get("params", {}))
    else:
        return error_response(request["id"], -32601, "Method not found")
    return {"jsonrpc": "2.0", "id": request["id"], "result": result}


def serve(
    box: Mailbox,
    harness: str,
    *,
    input_stream: TextIO | None = None,
    output_stream: TextIO | None = None,
) -> None:
    """Serve until EOF, isolating malformed frames from subsequent requests."""
    source = input_stream if input_stream is not None else sys.stdin
    sink = output_stream if output_stream is not None else sys.stdout
    for raw in source:
        request = None
        try:
            request = json.loads(raw)
            response = handle_request(box, harness, request)
        except Exception as exc:
            request_id = request.get("id") if isinstance(request, dict) else None
            response = error_response(request_id, -32600, str(exc))
        if response is not None:
            print(json.dumps(response, ensure_ascii=False), file=sink, flush=True)
