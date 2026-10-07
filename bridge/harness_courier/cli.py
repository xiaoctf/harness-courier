"""Command-line entry point shared by source scripts and python -m."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .errors import BridgeError
from .mailbox import Mailbox
from .mcp_server import serve
from .mcp_tools import invoke
from .paths import DEFAULT_DB, HARNESS


def build_parser() -> argparse.ArgumentParser:
    """Keep the existing command/argument contract in one discoverable place."""
    parser = argparse.ArgumentParser(
        description="Session-addressed local harness mailbox"
    )
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    commands = parser.add_subparsers(dest="command", required=True)
    mcp_parser = commands.add_parser("mcp")
    mcp_parser.add_argument("--harness", choices=HARNESS, required=True)
    call_parser = commands.add_parser("call")
    call_parser.add_argument("--harness", choices=HARNESS, default="codex")
    call_parser.add_argument("tool")
    call_parser.add_argument("--args", default="{}")
    call_parser.add_argument("--args-file", type=Path)
    commands.add_parser("peers")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run MCP until EOF or print one CLI result as UTF-8 JSON."""
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    args = build_parser().parse_args(argv)
    box = Mailbox(args.db)
    if args.command == "mcp":
        serve(box, args.harness)
        return 0
    try:
        if args.command == "peers":
            result = box.peers()
        else:
            raw = (
                args.args_file.read_text(encoding="utf-8")
                if args.args_file
                else args.args
            )
            result = invoke(box, args.harness, args.tool, json.loads(raw))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (BridgeError, TypeError, ValueError, OSError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False))
        return 1
