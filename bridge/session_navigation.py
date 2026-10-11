"""Guarded native selection of an existing local ZCode session, without OS input."""

import json
from pathlib import Path

from cu_client import DriverError


def select_zcode_session(client, binding, *, allow_busy_navigation=False):
    if binding.get("harness") != "zcode" or not binding.get("workspace_path"):
        raise DriverError("Native navigation requires the registered ZCode workspace")
    script = (
        Path(__file__).parent / "harness_courier" / "zcode_navigation.js"
    ).read_text(encoding="utf-8")
    request = {
        "sid": binding["session_id"],
        "workspace_path": binding["workspace_path"],
        "allow_busy_navigation": allow_busy_navigation is True,
    }
    result = client.call(
        "Runtime.evaluate",
        {
            "expression": "(" + script + ")(" + json.dumps(request) + ")",
            "returnByValue": True,
            "awaitPromise": True,
        },
    )
    if result.get("exceptionDetails"):
        raise DriverError("Native session navigation failed; no input attempted")
    value = result.get("result", {}).get("value")
    if not isinstance(value, dict):
        raise DriverError("Native session navigation returned an invalid observation")
    return value
