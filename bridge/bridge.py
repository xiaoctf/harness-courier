"""Compatibility entry point; implementation lives in harness_courier.

Existing desktop MCP registrations and receiver Hooks may keep importing this
module or invoking this file. The CLI arguments and database path are stable.
"""

from harness_courier.cli import main
from harness_courier.errors import BridgeError
from harness_courier.mailbox import Mailbox, now, valid_id
from harness_courier.mcp_server import serve
from harness_courier.mcp_tools import STRING, invoke, schema, tool_specs
from harness_courier.paths import DEFAULT_DB, HARNESS, ROOT

__all__ = [
    "BridgeError",
    "Mailbox",
    "ROOT",
    "DEFAULT_DB",
    "HARNESS",
    "STRING",
    "valid_id",
    "now",
    "schema",
    "tool_specs",
    "invoke",
    "serve",
    "main",
]

if __name__ == "__main__":
    raise SystemExit(main())
