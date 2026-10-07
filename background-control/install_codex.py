"""Legacy script path; implementation is in integrations/cua/install_codex.py."""

import runpy
from pathlib import Path

if __name__ == "__main__":
    runpy.run_path(
        str(
            Path(__file__).resolve().parents[1]
            / "integrations"
            / "cua"
            / "install_codex.py"
        ),
        run_name="__main__",
    )
