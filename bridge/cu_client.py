"""Small stdio MCP client for the already installed Windows desktop driver."""

from __future__ import annotations

import json
import os
import queue
import subprocess
import threading
import time
from pathlib import Path

from harness_courier.errors import DriverError

DEFAULT_DRIVER = Path(os.environ.get("LOCALAPPDATA", "")) / "KimiCU" / "kimi-cu.exe"


class DesktopClient:
    def __init__(self, driver: Path = DEFAULT_DRIVER):
        if not driver.is_file():
            raise DriverError(f"Desktop MCP executable missing: {driver}")
        self._id = 0
        self._lock = threading.Lock()
        self._responses: queue.Queue = queue.Queue()
        self._process = subprocess.Popen(
            [str(driver), "mcp"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        threading.Thread(target=self._read, daemon=True).start()
        self.call(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "harness-courier", "version": "0.1.0"},
            },
        )
        self._write({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def _read(self):
        try:
            for line in self._process.stdout:
                try:
                    self._responses.put(json.loads(line))
                except json.JSONDecodeError:
                    self._responses.put(DriverError("Driver emitted invalid JSON"))
        finally:
            self._responses.put(DriverError("Desktop MCP process exited"))

    def _write(self, value):
        try:
            self._process.stdin.write(json.dumps(value, ensure_ascii=False) + "\n")
            self._process.stdin.flush()
        except (OSError, ValueError) as exc:
            raise DriverError("Desktop MCP transport closed") from exc

    def call(self, method, params, timeout=20):
        with self._lock:
            self._id += 1
            request_id = self._id
            self._write(
                {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}
            )
            deadline = time.monotonic() + timeout
            while True:
                try:
                    response = self._responses.get(
                        timeout=max(0.01, deadline - time.monotonic())
                    )
                except queue.Empty as exc:
                    raise DriverError(
                        "Desktop MCP request timed out; re-observe before retrying"
                    ) from exc
                if isinstance(response, Exception):
                    raise response
                if response.get("id") != request_id:
                    continue
                if "error" in response:
                    raise DriverError(str(response["error"]))
                if "result" not in response:
                    raise DriverError("Invalid MCP response")
                return response["result"]

    def tool(self, name, arguments):
        result = self.call("tools/call", {"name": name, "arguments": arguments})
        if result.get("isError"):
            raise DriverError(
                "; ".join(c.get("text", "") for c in result.get("content", []))
            )
        if result.get("structuredContent"):
            return result["structuredContent"]
        for content in result.get("content", []):
            if content.get("type") == "text":
                try:
                    parsed = json.loads(content["text"])
                    if isinstance(parsed, dict):
                        if parsed.get("ok") is False:
                            raise DriverError(
                                str(parsed.get("error", "Desktop operation failed"))
                            )
                        return parsed
                except json.JSONDecodeError:
                    pass
        raise DriverError("Desktop tool did not return structured state")

    def close(self):
        if self._process.poll() is None:
            # Ending the MCP turn releases this driver's desktop-input lease.
            # Process termination alone can leave other CU sessions briefly busy.
            try:
                self.call(
                    "tools/call", {"name": "turn_ended", "arguments": {}}, timeout=3
                )
            except (DriverError, OSError, ValueError):
                pass
            self._process.terminate()
            try:
                self._process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self._process.kill()
                self._process.wait(timeout=3)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
