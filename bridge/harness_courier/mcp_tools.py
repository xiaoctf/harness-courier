"""Harness-scoped tool schemas and dispatch, independent of stdio framing."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from dispatch_runner import tool_annotations

from .errors import BridgeError
from .mailbox import Mailbox
from .tool_names import LEGACY_TO_CANONICAL, legacy_name


def schema(properties: dict, required: tuple | list = ()) -> dict:
    return {
        "type": "object",
        "properties": properties,
        "required": list(required),
        "additionalProperties": False,
    }


STRING = {"type": "string"}


def tool_specs(harness: str) -> list[dict]:
    """Expose only the controller or receiver tools for this harness."""
    common = [
        (
            "bridge_peers",
            "List registered actual desktop sessions and persistent alias bindings.",
            schema({}),
        ),
        (
            "bridge_status",
            "Read a message's true receiver ACK/result, not just GUI input success.",
            schema({"message_id": STRING}, ["message_id"]),
        ),
        (
            "bridge_wait",
            "Wait up to 55 seconds for a real ACK/result; controller waits also report idle/Stop with a missing receipt. Read wait_reason and agent. Never infer success or resend from timeout.",
            schema(
                {
                    "message_id": STRING,
                    "timeout": {"type": "number", "minimum": 0, "maximum": 55},
                    "until": {"enum": ["acknowledged", "completed"]},
                },
                ["message_id"],
            ),
        ),
    ]
    if harness == "codex":
        common += [
            (
                "bridge_agent_status",
                "Read the bound agent's current turn, last passive Hook and unfinished receipts. Never navigates or wakes it. Idle/Stop does not prove task completion; unknown stays unknown.",
                schema(
                    {
                        "alias": STRING,
                        "observe_native": {"type": "boolean", "default": True},
                        "stale_after_seconds": {
                            "type": "number",
                            "minimum": 30,
                            "maximum": 86400,
                            "default": 300,
                        },
                    },
                    ["alias"],
                ),
            ),
            (
                "bridge_bind",
                "Bind an alias to an explicitly chosen real session ID. Do not guess recipients.",
                schema(
                    {
                        "alias": STRING,
                        "harness": {"enum": ["kimi", "zcode"]},
                        "session_id": STRING,
                        "title": STRING,
                        "replace": {"type": "boolean"},
                    },
                    ["alias", "harness", "session_id"],
                ),
            ),
            (
                "bridge_send",
                "Send to a bound desktop chat and attempt desktop wake. Receipts arrive separately.",
                schema(
                    {
                        "alias": STRING,
                        "body": STRING,
                        "sender_session_id": STRING,
                        "dispatch": {"type": "boolean"},
                        "allow_busy_navigation": {
                            "type": "boolean",
                            "default": True,
                            "description": "Allow selecting the bound chat while another chat is generating; preserves drafts.",
                        },
                        "priority": {
                            "type": "boolean",
                            "default": True,
                            "description": "Native priority delivery; may interrupt the bound target. Mailbox force_priority policy overrides false. Requires authorization.",
                        },
                    },
                    ["alias", "body"],
                ),
            ),
            (
                "bridge_dispatch",
                "Retry a queued message or a delivered message whose desktop wake failed; never retry after ACK.",
                schema(
                    {
                        "message_id": STRING,
                        "allow_busy_navigation": {"type": "boolean", "default": True},
                        "priority": {"type": "boolean", "default": True},
                    },
                    ["message_id"],
                ),
            ),
        ]
    else:
        common += [
            (
                "bridge_inbox",
                "Read messages for YOUR exact session ID given by the receiver Hook. Never use another chat's ID.",
                schema(
                    {"caller_session_id": STRING, "message_id": STRING},
                    ["caller_session_id"],
                ),
            ),
            (
                "bridge_ack",
                "Confirm that you have read this message in your bound chat.",
                schema(
                    {"caller_session_id": STRING, "message_id": STRING},
                    ["caller_session_id", "message_id"],
                ),
            ),
            (
                "bridge_reply",
                "Return your result or failure to the sending Codex chat's message record.",
                schema(
                    {
                        "caller_session_id": STRING,
                        "message_id": STRING,
                        "body": STRING,
                        "result_kind": {"enum": ["completed", "failed"]},
                    },
                    ["caller_session_id", "message_id", "body"],
                ),
            ),
        ]
    canonical = [
        {
            "name": LEGACY_TO_CANONICAL[n],
            "description": d,
            "inputSchema": s,
            "annotations": tool_annotations(n),
        }
        for n, d, s in common
    ]
    aliases = [
        {
            **tool,
            "name": old,
            "description": f"Legacy alias for {tool['name']}. {tool['description']}",
        }
        for tool, (old, _, _) in zip(canonical, common, strict=True)
    ]
    return canonical + aliases


def invoke(
    box: Mailbox,
    harness: str,
    name: str,
    arguments: Any,
    *,
    dispatch_handler: Callable[[str], dict] | None = None,
) -> Any:
    """Validate the tool surface before routing; never mutate caller arguments."""
    spec = next((t for t in tool_specs(harness) if t["name"] == name), None)
    if spec is None:
        raise BridgeError("Unknown or unavailable tool for this harness")
    fields = spec["inputSchema"]
    if (
        not isinstance(arguments, dict)
        or set(arguments) - set(fields["properties"])
        or set(fields["required"]) - set(arguments)
    ):
        raise BridgeError("Invalid tool arguments")
    name = legacy_name(name)
    if name == "bridge_agent_status":
        from agent_activity import agent_status

        return agent_status(box, **arguments)
    if name in ("bridge_status", "bridge_wait") and harness == "codex":
        from agent_activity import wait_with_activity, with_activity

        if name == "bridge_wait":
            return wait_with_activity(box, **arguments)
        return with_activity(box, box.status(**arguments))
    direct = {
        "bridge_peers": box.peers,
        "bridge_status": box.status,
        "bridge_wait": box.wait,
        "bridge_bind": box.bind,
        "bridge_dispatch": dispatch_handler or box.dispatch,
    }
    if name in direct:
        if name == "bridge_dispatch":
            arguments = {"allow_busy_navigation": True, "priority": True, **arguments}
        return direct[name](**arguments)
    args = dict(arguments)
    if name == "bridge_send":
        should_dispatch = args.pop("dispatch", True)
        options = {
            key: args.pop(key)
            for key in ("allow_busy_navigation", "priority")
            if key in args
        }
        if type(should_dispatch) is not bool or any(
            type(v) is not bool for v in options.values()
        ):
            raise BridgeError("Delivery options must be booleans")
        if not should_dispatch and any(options.values()):
            raise BridgeError(
                "Priority/navigation requires dispatch=true; no message queued"
            )
        message = box.send(
            sender_harness=harness,
            dispatch=False,
            allow_offline=should_dispatch,
            **args,
        )
        if not should_dispatch:
            return message
        options = {"allow_busy_navigation": True, "priority": True, **options}
        return (dispatch_handler or box.dispatch)(message["id"], **options)
    session_id = args.pop("caller_session_id")
    receiver = {
        "bridge_inbox": box.receive,
        "bridge_ack": box.acknowledge,
        "bridge_reply": box.reply,
    }
    return receiver[name](harness, session_id, **args)
