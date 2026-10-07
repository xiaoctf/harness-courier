"""Domain errors that can be returned safely to MCP and CLI callers."""


class BridgeError(ValueError):
    """A rejected mailbox operation or invalid caller input."""


class DriverError(RuntimeError):
    """A rejected desktop transport operation or unavailable local endpoint."""
