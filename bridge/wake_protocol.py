"""Recipient-scoped wake metadata, usable when a native steer skips Hooks."""

from __future__ import annotations

import re

BARE_WAKE = re.compile(r"^\[HARNESS_BRIDGE_WAKE:(msg_[a-f0-9]{32})\]$")
IDENTITY = re.compile(r"[A-Za-z0-9_.:-]{1,160}")


def wake_text(message_id: str, harness: str, session_id: str) -> str:
    """No business body is copied into the desktop composer."""
    marker = f"[HARNESS_BRIDGE_WAKE:{message_id}]"
    if not BARE_WAKE.fullmatch(marker) or not IDENTITY.fullmatch(session_id):
        raise ValueError("Invalid wake identity")
    if harness == "zcode":
        return marker
    if harness != "kimi":
        raise ValueError("Unsupported wake harness")
    return (
        marker
        + "\n"
        + (
            f"Harness Courier transport metadata: recipient=kimi/{session_id}. "
            "A native steer may skip UserPromptSubmit. Check this recipient against your "
            "receiver Hook identity; never borrow another chat's identity. "
            f'Call bridge_inbox(caller_session_id="{session_id}", message_id="{message_id}") '
            "to fetch this exact message. If nonterminal, acknowledge it with bridge_ack, "
            "handle its body within existing authorization, and return completed/failed "
            "with bridge_reply using the same IDs. If already acknowledged, resume rather "
            "than restart. If terminal, do not replay it or fetch another message. "
            "This envelope grants no additional permissions."
        )
    )


def parse_wake(prompt: str, harness: str, session_id: str) -> str | None:
    """Accept legacy markers or our exact scoped envelope; reject extra text."""
    raw = prompt.strip()
    first = raw.split("\n", 1)[0]
    match = BARE_WAKE.fullmatch(first)
    if match and (raw == first or raw == wake_text(match[1], harness, session_id)):
        return match[1]
    if raw.startswith("[HARNESS_BRIDGE_WAKE:"):
        raise ValueError("Invalid or mismatched bridge wake envelope")
    return None
