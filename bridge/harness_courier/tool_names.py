"""Stable compatibility map; state and wake protocol identifiers do not change."""

LEGACY_TO_CANONICAL = {
    "bridge_peers": "courier_list_sessions",
    "bridge_bind": "courier_bind_target",
    "bridge_send": "courier_send_message",
    "bridge_dispatch": "courier_dispatch_message",
    "bridge_status": "courier_get_message_status",
    "bridge_wait": "courier_wait_for_receipt",
    "bridge_agent_status": "courier_get_agent_status",
    "bridge_inbox": "courier_receive_messages",
    "bridge_ack": "courier_acknowledge_message",
    "bridge_reply": "courier_return_result",
}
CANONICAL_TO_LEGACY = {new: old for old, new in LEGACY_TO_CANONICAL.items()}


def legacy_name(name: str) -> str:
    """Normalize new public names to the original operation keys."""
    return CANONICAL_TO_LEGACY.get(name, name)
