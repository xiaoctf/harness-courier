"""Idempotent append-only Codex MCP registration; preserve all existing settings."""

import hashlib
import json
import os
import tempfile
import tomllib
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CONFIG = Path.home() / ".codex" / "config.toml"
BINARY = ROOT / "runtime" / "cua-driver-rs-0.33.3-windows-x86_64" / "cua-driver.exe"
POLICY = ROOT / "background-policy.yaml"
NAME = "cua_background"
EXPECTED = {
    "command": str(BINARY),
    "args": ["mcp", "--direct"],
    "startup_timeout_sec": 30,
    "tool_timeout_sec": 45,
    "env": {
        "CUA_DRIVER_POLICY_FILE": str(POLICY),
        "CUA_DRIVER_PERMISSION_MODE": "standard",
        "CUA_DRIVER_RS_TELEMETRY_ENABLED": "0",
        "CUA_DRIVER_TELEMETRY_HOME": str(ROOT / "data"),
    },
}


def sha(data):
    return hashlib.sha256(data).hexdigest()


def registration_bytes(old_bytes: bytes, expected: dict) -> bytes:
    """Append one owned server, preserving all prior configuration semantically."""
    old = tomllib.loads(old_bytes.decode("utf-8-sig"))
    servers = old.get("mcp_servers", {})
    if NAME in servers:
        if servers[NAME] != expected:
            raise RuntimeError(
                "Existing cua_background entry differs; refusing overwrite"
            )
        return old_bytes

    def quote(value):
        return json.dumps(value, ensure_ascii=False)

    text = "\n\n[mcp_servers.cua_background]\n"
    for key in ["command", "args", "startup_timeout_sec", "tool_timeout_sec"]:
        text += key + " = " + quote(expected[key]) + "\n"
    text += "\n[mcp_servers.cua_background.env]\n"
    for key, value in expected["env"].items():
        text += key + " = " + quote(value) + "\n"
    new_bytes = old_bytes + text.encode("utf-8")
    preserved = tomllib.loads(new_bytes.decode("utf-8-sig"))
    if preserved["mcp_servers"].pop(NAME) != expected:
        raise RuntimeError("MCP registration changed unexpectedly")
    if "mcp_servers" not in old:
        del preserved["mcp_servers"]
    if preserved != old:
        raise RuntimeError("Existing configuration would change")
    return new_bytes


def main():
    if not BINARY.is_file() or not POLICY.is_file():
        raise RuntimeError("Driver binary or background policy missing")
    old_bytes = CONFIG.read_bytes()
    new_bytes = registration_bytes(old_bytes, EXPECTED)
    if new_bytes == old_bytes:
        print("Already registered; configuration unchanged.")
        return
    backups = ROOT / "backups"
    backups.mkdir(exist_ok=True)
    backup = backups / ("codex-config-before-" + sha(old_bytes)[:12] + ".toml")
    if backup.exists() and backup.read_bytes() != old_bytes:
        raise RuntimeError("Backup collision")
    backup.write_bytes(old_bytes)
    if CONFIG.read_bytes() != old_bytes:
        raise RuntimeError("Configuration changed concurrently; retry after inspection")
    fd, temporary = tempfile.mkstemp(
        prefix="cua-background-", suffix=".tmp", dir=CONFIG.parent
    )
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(new_bytes)
        if CONFIG.read_bytes() != old_bytes:
            raise RuntimeError("Configuration changed concurrently; refusing replace")
        os.replace(temporary, CONFIG)
    finally:
        if Path(temporary).exists():
            Path(temporary).unlink()
    verified = CONFIG.read_bytes()
    if verified != new_bytes:
        raise RuntimeError("Post-write verification failed")
    receipt = {
        "utc": datetime.now(timezone.utc).isoformat(),
        "server": NAME,
        "version": "0.33.3",
        "config": str(CONFIG),
        "backup": str(backup),
        "before_sha256": sha(old_bytes),
        "after_sha256": sha(verified),
        "existing_configuration_preserved": True,
        "policy_sha256": sha(POLICY.read_bytes()),
        "mcp": EXPECTED,
        "codex_runtime_reload_verified": False,
    }
    (ROOT / "verification").mkdir(parents=True, exist_ok=True)
    (ROOT / "verification" / "install-receipt.json").write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        "Registered cua_background; all existing configuration preserved; backup verified."
    )


if __name__ == "__main__":
    main()
