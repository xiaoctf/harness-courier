"""Compatibility alias for install_integrations."""

import sys
from importlib import import_module

if __name__ == "__main__":
    import_module("install_integrations").main()
else:
    sys.modules[__name__] = import_module("install_integrations")
