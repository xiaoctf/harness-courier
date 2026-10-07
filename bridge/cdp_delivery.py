"""Compatibility alias for cdp_transport."""

import sys
from importlib import import_module

sys.modules[__name__] = import_module("cdp_transport")
