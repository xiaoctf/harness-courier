"""One application registry for both CDP delivery and desktop startup.

Generic Windows locations are examples, not app discovery. A local TOML file
or HARNESS_COURIER_APPS (with legacy fallback) overrides them without editing Python source.
"""

from __future__ import annotations

import os
import tomllib
from pathlib import Path

from .errors import DriverError
from .paths import ROOT

AppRegistry = dict[str, tuple[Path, int]]
DEFAULT_APPS: AppRegistry = {
    "kimi": (Path(r"C:\Program Files\Kimi Code\Kimi Code.exe"), 49371),
    "zcode": (Path(r"C:\Program Files\ZCode\ZCode.exe"), 49372),
}


def load_apps(config_path: Path | None = None) -> AppRegistry:
    """Read validated optional overrides; an explicit missing file is an error."""
    if config_path is None:
        override = os.environ.get("HARNESS_COURIER_APPS") or os.environ.get(
            "HARNESS_BRIDGE_APPS"
        )
        config_path = Path(override) if override else ROOT.parent / "apps.toml"
        if not override and not config_path.exists():
            return dict(DEFAULT_APPS)
    try:
        config = tomllib.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise DriverError(f"Cannot read application config: {config_path}") from exc
    if set(config) != {"apps"} or not isinstance(config["apps"], dict):
        raise DriverError(
            "Application config must contain only [apps.kimi] / [apps.zcode]"
        )
    apps = dict(DEFAULT_APPS)
    for name, spec in config["apps"].items():
        if (
            name not in apps
            or not isinstance(spec, dict)
            or set(spec) - {"executable", "port"}
        ):
            raise DriverError("Unknown application or config field")
        old_executable, old_port = apps[name]
        executable = spec.get("executable", str(old_executable))
        port = spec.get("port", old_port)
        if not isinstance(executable, str) or not executable.strip():
            raise DriverError(f"{name}: executable must be a nonempty absolute path")
        path = Path(os.path.expandvars(executable)).expanduser()
        if not path.is_absolute():
            raise DriverError(f"{name}: executable must be an absolute path")
        if type(port) is not int or not 1024 <= port <= 65535:
            raise DriverError(f"{name}: port must be an integer from 1024 to 65535")
        apps[name] = (path, port)
    if len({port for _, port in apps.values()}) != len(apps):
        raise DriverError("Applications must use distinct debugging ports")
    return apps


def verified_port(harness: str, apps: AppRegistry) -> int | None:
    """Return the port only for one loopback listener owned by the chosen app.

    No listener means the app is not ready. A foreign/ambiguous listener is a
    hard error; it must never be mistaken for permission to launch or connect.
    """
    import psutil

    executable, port = apps[harness]
    listeners = [
        c
        for c in psutil.net_connections(kind="tcp")
        if c.status == psutil.CONN_LISTEN and c.laddr.port == port
    ]
    if not listeners:
        return None
    if (
        len(listeners) != 1
        or listeners[0].laddr.ip != "127.0.0.1"
        or not listeners[0].pid
    ):
        raise DriverError("Debug endpoint must have one verified IPv4 loopback owner")
    try:
        actual = Path(psutil.Process(listeners[0].pid).exe())
    except (psutil.Error, OSError) as exc:
        raise DriverError("Cannot verify debug endpoint owner") from exc
    if os.path.normcase(str(actual)) != os.path.normcase(str(executable)):
        raise DriverError(
            "Debug endpoint belongs to another executable; no connection made"
        )
    return port


APPS = load_apps()
