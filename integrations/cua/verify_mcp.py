"""Fresh MCP smoke check using the exact registered Codex command and environment."""

import json
import os
import queue
import subprocess
import threading
import tomllib
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def main():
    config = tomllib.loads(
        (Path.home() / ".codex" / "config.toml").read_text(encoding="utf-8-sig")
    )
    spec = config["mcp_servers"]["cua_background"]
    proc = subprocess.Popen(
        [spec["command"], *spec["args"]],
        env={**os.environ, **spec["env"]},
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        encoding="utf-8",
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    responses = queue.Queue()

    def read():
        try:
            for line in proc.stdout:
                responses.put(json.loads(line))
        finally:
            responses.put(RuntimeError("MCP exited"))

    threading.Thread(target=read, daemon=True).start()
    sequence = 0

    def rpc(method, params):
        nonlocal sequence
        sequence += 1
        proc.stdin.write(
            json.dumps(
                {"jsonrpc": "2.0", "id": sequence, "method": method, "params": params}
            )
            + "\n"
        )
        proc.stdin.flush()
        while True:
            response = responses.get(timeout=15)
            if isinstance(response, Exception):
                raise response
            if response.get("id") == sequence:
                return response

    try:
        initialized = rpc(
            "initialize",
            {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "codex-config-verifier", "version": "1.0"},
            },
        )["result"]
        assert initialized["serverInfo"]["version"] == "0.33.3"
        proc.stdin.write('{"jsonrpc":"2.0","method":"notifications/initialized"}\n')
        proc.stdin.flush()
        names = [t["name"] for t in rpc("tools/list", {})["result"]["tools"]]
        assert "click" in names and "get_window_state" in names
        assert "bring_to_front" not in names and "launch_app" not in names
        denial = rpc(
            "tools/call",
            {
                "name": "click",
                "arguments": {
                    "pid": 0,
                    "window_id": 0,
                    "x": 0,
                    "y": 0,
                    "delivery_mode": "foreground",
                },
            },
        )["result"]
        assert (
            denial.get("isError")
            and denial["structuredContent"]["code"] == "permission_denied"
        )
        receipt = {
            "utc": datetime.now(timezone.utc).isoformat(),
            "serverInfo": initialized["serverInfo"],
            "protocolVersion": initialized["protocolVersion"],
            "tools": names,
            "foreground_denied": True,
            "configured_command_verified": True,
            "codex_desktop_reload_verified": False,
        }
        (ROOT / "verification").mkdir(parents=True, exist_ok=True)
        (ROOT / "verification" / "mcp-smoke.json").write_text(
            json.dumps(receipt, indent=2) + "\n", encoding="utf-8"
        )
        print(
            "PASS: fresh configured MCP initialized; background tools discovered; foreground denied."
        )
    finally:
        proc.stdin.close()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.terminate()
            proc.wait(timeout=5)


if __name__ == "__main__":
    main()
