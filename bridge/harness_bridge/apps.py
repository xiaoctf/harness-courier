"""Compatibility alias for harness_courier.app_registry."""

import sys
from importlib import import_module

sys.modules[__name__] = import_module("harness_courier.app_registry")
