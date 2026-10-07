"""Compatibility alias for harness_courier.mcp_tools."""

import sys
from importlib import import_module

sys.modules[__name__] = import_module("harness_courier.mcp_tools")
