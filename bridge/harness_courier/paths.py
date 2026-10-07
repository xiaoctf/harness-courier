"""Stable source-tree paths shared by the legacy entry points."""

from pathlib import Path

# Keep existing databases next to bridge.py after extracting this package.
ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = ROOT / "data" / "bridge.sqlite3"
HARNESS = ("codex", "kimi", "zcode")
