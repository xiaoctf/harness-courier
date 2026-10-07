"""Shared Windows launcher; preserves profiles and never closes running apps."""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import subprocess
import sys
import time
from urllib.request import ProxyHandler, build_opener

import psutil

from .app_registry import APPS, verified_port
from .paths import ROOT


def running_app(path):
    for process in psutil.process_iter(["exe"]):
        try:
            if process.info["exe"] and os.path.normcase(
                process.info["exe"]
            ) == os.path.normcase(str(path)):
                return True
        except psutil.Error:
            continue
    return False


def endpoint_ready(name: str) -> bool:
    """Check listener ownership before making a bounded local HTTP request."""
    port = verified_port(name, APPS)
    if port is None:
        return False
    opener = build_opener(ProxyHandler({}))
    try:
        with opener.open(
            f"http://127.0.0.1:{port}/json/version", timeout=2
        ) as response:
            info = json.loads(response.read(65536))
        return isinstance(info, dict) and bool(info.get("Protocol-Version"))
    except (OSError, ValueError):
        return False


def launch_apps(names: list[str], *, check: bool = False) -> int:
    """Preflight every selected app before starting any; --check is read-only."""
    pending = []
    for name in names:
        path, port = APPS[name]
        if not path.is_file():
            raise RuntimeError(f"Application missing: {path}")
        ready = endpoint_ready(name)
        running = running_app(path)
        print(
            f"{name}: executable OK; running={running}; background_ready={ready}",
            flush=True,
        )
        if check:
            continue
        if running and not ready:
            raise RuntimeError(
                f"{name} is already running without a ready background endpoint. Save work and fully quit it, then run this launcher again."
            )
        if not running:
            pending.append(name)
    if check:
        print("CHECK COMPLETE: no applications started or closed.")
        return 0
    for name in pending:
        path, port = APPS[name]
        startup = subprocess.STARTUPINFO()
        startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startup.wShowWindow = 7  # SW_SHOWMINNOACTIVE
        subprocess.Popen(
            [
                str(path),
                f"--remote-debugging-port={port}",
                "--remote-debugging-address=127.0.0.1",
            ],
            creationflags=subprocess.CREATE_NO_WINDOW,
            startupinfo=startup,
            cwd=path.parent,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    deadline = time.monotonic() + 25
    while time.monotonic() < deadline:
        if all(endpoint_ready(name) for name in names):
            print(
                "READY: desktop background endpoints verified. Profiles and existing chats retained."
            )
            print(
                "NOTE: mailbox registration and session bindings are configured separately."
            )
            return 0
        time.sleep(0.5)
    raise RuntimeError(
        "Endpoint startup was not verified. Check application startup; no foreground delivery fallback."
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Start Kimi/ZCode with verified loopback background endpoints."
    )
    parser.add_argument("--app", choices=("all", "kimi", "zcode"), default="all")
    parser.add_argument(
        "--check",
        action="store_true",
        help="Read-only check; never start or close apps.",
    )
    parser.add_argument(
        "--autostart",
        action="store_true",
        help="Run quietly at login and append a startup log.",
    )
    args = parser.parse_args(argv)
    names = list(APPS) if args.app == "all" else [args.app]
    try:
        if args.autostart:
            log_path = ROOT / "data" / "startup.log"
            log_path.parent.mkdir(parents=True, exist_ok=True)
            with log_path.open("a", encoding="utf-8", buffering=1) as log:
                with contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
                    print(
                        time.strftime("\n[%Y-%m-%d %H:%M:%S] Login startup"), flush=True
                    )
                    if not args.check:
                        time.sleep(15)
                    try:
                        return launch_apps(names, check=args.check)
                    except (OSError, RuntimeError, psutil.Error) as error:
                        print("ERROR: " + str(error), file=sys.stderr)
                        return 1
        return launch_apps(names, check=args.check)
    except (OSError, RuntimeError, psutil.Error) as error:
        print("ERROR: " + str(error), file=sys.stderr)
        return 1


def run() -> int:
    """Interactive CMD behavior; automated callers never wait for Enter."""
    code = main()
    if "--check" not in sys.argv and "--autostart" not in sys.argv:
        try:
            input("Press Enter to close this launcher...")
        except EOFError:
            pass
    return code
