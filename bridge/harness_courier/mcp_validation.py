"""Validate the primitive schemas used by Courier before any mailbox mutation."""

import math


def validate_arguments(fields: dict, arguments: object) -> None:
    """Reject type coercion, unknown keys, missing fields and non-finite numbers.

    This deliberately supports only Courier's flat primitive tool schemas.
    New schema types must add validation here instead of silently passing.
    """
    if (
        not isinstance(arguments, dict)
        or set(arguments) - set(fields["properties"])
        or set(fields["required"]) - set(arguments)
    ):
        raise ValueError("Invalid tool arguments")
    for name, value in arguments.items():
        spec = fields["properties"][name]
        kind = spec.get("type")
        valid = False
        if kind == "string":
            valid = isinstance(value, str)
        elif kind == "boolean":
            valid = type(value) is bool
        elif kind == "number":
            valid = (
                type(value) in (int, float)
                and math.isfinite(value)
                and spec.get("minimum", -math.inf) <= value
                and value <= spec.get("maximum", math.inf)
            )
        elif kind is None and "enum" in spec:
            valid = any(
                type(value) is type(item) and value == item for item in spec["enum"]
            )
        if not valid:
            # Field names are schema-owned; never echo a caller's payload.
            raise ValueError(f"Invalid tool argument: {name}")
