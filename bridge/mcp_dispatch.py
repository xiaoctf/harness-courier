"""Compatibility alias for dispatch_runner."""

import sys
from importlib import import_module

sys.modules[__name__] = import_module("dispatch_runner")
