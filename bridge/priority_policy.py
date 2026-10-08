"""Mailbox-local policy overrides caller priority without changing host access."""

import json
from pathlib import Path


def resolve_priority(database, requested):
    if type(requested) is not bool:
        raise ValueError("Priority must be a boolean")
    policy = Path(database).resolve().parent / "delivery-policy.json"
    if not policy.exists():
        return requested
    if policy.stat().st_size > 4096:
        raise ValueError("Delivery policy is too large")
    settings = json.loads(policy.read_text(encoding="utf-8-sig"))
    if not isinstance(settings, dict) or set(settings) != {"force_priority"}:
        raise ValueError("Delivery policy requires only force_priority")
    if type(settings["force_priority"]) is not bool:
        raise ValueError("force_priority must be a boolean")
    return settings["force_priority"] or requested
