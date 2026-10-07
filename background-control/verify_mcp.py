"""Legacy script path; implementation is in integrations/cua/verify_mcp.py."""

import runpy
from pathlib import Path

if __name__ == "__main__":
    runpy.run_path(
        str(
            Path(__file__).resolve().parents[1]
            / "integrations"
            / "cua"
            / "verify_mcp.py"
        ),
        run_name="__main__",
    )
