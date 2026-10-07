"""Add only this bridge's MCP and receiver Hooks; preserve existing app settings."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
import tomllib
from pathlib import Path

from bridge import ROOT

NAME = "harness_courier"
LEGACY_NAME = "harness_bridge"
# Owned Hook block markers stay stable to prevent duplicate Hooks after upgrades.
START = "# BEGIN HARNESS BRIDGE"
END = "# END HARNESS BRIDGE"


def digest(data):
    return hashlib.sha256(data).hexdigest()


def atomic_write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        dir=path.parent, delete=False, prefix=".harness-bridge-", suffix=".tmp"
    ) as f:
        temporary = Path(f.name)
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    try:
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def server(python, harness):
    return {
        "command": str(python),
        "args": [str(ROOT / "bridge.py"), "mcp", "--harness", harness],
    }


def registered_name(servers, expected, client):
    """Keep an existing owned registration, and never create a second alias."""
    present = [name for name in (NAME, LEGACY_NAME) if name in servers]
    for name in present:
        spec = servers[name]
        if not isinstance(spec, dict) or any(
            spec.get(k) != v for k, v in expected.items()
        ):
            raise ValueError(f"Unmanaged {client} MCP name conflict: {name}")
    if len(present) > 1:
        raise ValueError(
            f"Duplicate {client} Courier/Bridge registrations; inspect before installing"
        )
    return present[0] if present else NAME


def toml_section(python):
    s = server(python, "codex")
    return f"\n{START}\n[mcp_servers.{NAME}]\ncommand = {json.dumps(s['command'])}\nargs = {json.dumps(s['args'])}\n{END}\n"


def kimi_hook_section(python):
    command = subprocess.list2cmdline(
        [str(python), str(ROOT / "receiver_hook.py"), "--harness", "kimi"]
    )
    entries = [
        f"[[hooks]]\nevent = {json.dumps(event)}\ncommand = {json.dumps(command)}\ntimeout = 5\n"
        for event in ("SessionStart", "UserPromptSubmit", "SessionEnd")
    ]
    return "\n" + START + "\n" + "\n".join(entries) + END + "\n"


def build_changes(home, python):
    # Config values are opaque here: no credentials or unrelated settings are logged.
    plans = []
    codex = home / ".codex" / "config.toml"
    raw = codex.read_bytes() if codex.exists() else b""
    parsed = tomllib.loads(raw.decode("utf-8-sig"))
    expected = server(python, "codex")
    servers = parsed.get("mcp_servers", {})
    name = registered_name(servers, expected, "Codex")
    new = raw if name in servers else raw + toml_section(python).encode("utf-8")
    tomllib.loads(new.decode("utf-8-sig"))
    plans.append((codex, raw, new, "codex-mcp"))

    kimi = home / ".kimi-code" / "mcp.json"
    raw = kimi.read_bytes() if kimi.exists() else b""
    parsed = json.loads(raw.decode("utf-8-sig")) if raw else {}
    new_object = copy.deepcopy(parsed)
    servers = new_object.setdefault("mcpServers", {})
    expected = server(python, "kimi")
    name = registered_name(servers, expected, "Kimi")
    if name not in servers:
        servers[name] = expected
    new = (
        raw
        if parsed == new_object
        else (json.dumps(new_object, ensure_ascii=False, indent=2) + "\n").encode(
            "utf-8"
        )
    )
    # Assert every pre-existing MCP entry survives unchanged.
    assert all(
        new_object["mcpServers"].get(k) == v
        for k, v in parsed.get("mcpServers", {}).items()
    )
    plans.append((kimi, raw, new, "kimi-mcp"))

    kimi_config = home / ".kimi-code" / "config.toml"
    raw = kimi_config.read_bytes() if kimi_config.exists() else b""
    old_object = tomllib.loads(raw.decode("utf-8-sig"))
    if START.encode() in raw:
        new = raw
    else:
        new = raw + kimi_hook_section(python).encode("utf-8")
    new_object = tomllib.loads(new.decode("utf-8-sig"))
    assert all(new_object[k] == v for k, v in old_object.items() if k != "hooks")
    assert new_object.get("hooks", [])[
        : len(old_object.get("hooks", []))
    ] == old_object.get("hooks", [])
    plans.append((kimi_config, raw, new, "kimi-hooks"))

    zcode = home / ".zcode" / "cli" / "config.json"
    raw = zcode.read_bytes() if zcode.exists() else b""
    parsed = json.loads(raw.decode("utf-8-sig")) if raw else {}
    new_object = copy.deepcopy(parsed)
    servers = new_object.setdefault("mcp", {}).setdefault("servers", {})
    expected = server(python, "zcode")
    name = registered_name(servers, expected, "ZCode")
    if name not in servers:
        servers[name] = expected
    hooks = new_object.setdefault("hooks", {})
    if hooks.get("enabled") is False and any(hooks.get("events", {}).values()):
        raise ValueError(
            "ZCode has disabled pre-existing Hooks; enabling them needs a separate scope decision"
        )
    hooks["enabled"] = True
    events = hooks.setdefault("events", {})
    expected_hook = {
        "type": "process",
        "command": str(python),
        "args": [str(ROOT / "receiver_hook.py"), "--harness", "zcode"],
        "enabled": True,
        "timeoutMs": 5000,
    }
    for event in ("SessionStart", "UserPromptSubmit"):
        rules = events.setdefault(event, [])
        matches = [
            h
            for rule in rules
            for h in rule.get("hooks", [])
            if h.get("args") == expected_hook["args"]
        ]
        if matches and any(h != expected_hook for h in matches):
            raise ValueError("Unmanaged ZCode receiver Hook conflict")
        if not matches:
            rules.append({"hooks": [expected_hook.copy()]})
    assert all(
        new_object[k] == v for k, v in parsed.items() if k not in ("mcp", "hooks")
    )
    assert all(
        new_object["mcp"][k] == v
        for k, v in parsed.get("mcp", {}).items()
        if k != "servers"
    )
    assert all(
        servers[k] == v for k, v in parsed.get("mcp", {}).get("servers", {}).items()
    )
    new = (
        raw
        if parsed == new_object
        else (json.dumps(new_object, ensure_ascii=False, indent=2) + "\n").encode(
            "utf-8"
        )
    )
    plans.append((zcode, raw, new, "zcode-mcp-and-hooks"))
    return plans


def install(home, python, apply=False):
    plans = build_changes(home, python)
    receipt = []
    written = []
    try:
        for path, before, after, part in plans:
            item = {
                "part": part,
                "path": str(path),
                "changed": before != after,
                "before_sha256": digest(before),
                "after_sha256": digest(after),
            }
            if apply and before != after:
                # Re-read immediately before every mutation and refuse concurrent user edits.
                fresh = path.read_bytes() if path.exists() else b""
                if fresh != before:
                    raise RuntimeError(
                        "Configuration changed while preparing installation"
                    )
                if path.exists():
                    backup = path.with_name(
                        path.name + f".harness-bridge-{time.time_ns()}.bak"
                    )
                    backup.write_bytes(before)
                    item["backup_path"] = str(backup)
                atomic_write(path, after)
                written.append((path, before, after))
            receipt.append(item)
    except BaseException:
        for path, before, after in reversed(written):
            if path.read_bytes() == after:
                atomic_write(path, before)
        raise
    return {
        "applied": apply,
        "configuration": receipt,
        "desktop_restart_performed": False,
        "live_roundtrip_verified": False,
    }


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser()
    p.add_argument("--apply", action="store_true")
    args = p.parse_args()
    report = install(Path.home(), Path(sys.executable), args.apply)
    if args.apply:
        target = ROOT / "verification"
        target.mkdir(exist_ok=True)
        atomic_write(
            target / "install-receipt.json",
            (json.dumps(report, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
        )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
