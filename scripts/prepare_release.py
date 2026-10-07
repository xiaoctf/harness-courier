"""Export reviewed tracked source without local history, runtime data or binaries."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOP_LEVEL = {
    ".gitattributes",
    ".gitignore",
    "README.md",
    "README.zh-CN.md",
    "LICENSE",
    "THIRD_PARTY_NOTICES.md",
    "CONTRIBUTING.md",
    "SECURITY.md",
    "pyproject.toml",
    "apps.example.toml",
}
SOURCE_DIRS = {
    "bridge",
    "background-control",
    "integrations",
    "launcher",
    "docs",
    ".github",
    "scripts",
}
EXCLUDED_PARTS = {
    ".git",
    ".venv",
    "__pycache__",
    "data",
    "backups",
    "runtime",
    "downloads",
    "verification",
    "test-tmp",
    "test-workspaces",
    "research",
    "build",
    "dist",
}
TEXT_SUFFIXES = {".py", ".js", ".cs", ".md", ".toml", ".yaml", ".yml", ".cmd"}
PATTERNS = {
    "credential_candidate": re.compile(
        r"(?:sk-[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9]{20,}|"
        r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----)"
    ),
    "machine_path": re.compile(
        r"\b[A-Z]:[\\/](?:Users[\\/][^\\/\s\"'<>]+|application[\\/]|"
        r"AAresource[\\/]|harness-bridge[\\/]|cua-driver[\\/]|Python[\\/])",
        re.IGNORECASE,
    ),
}


def selected_files(root: Path) -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files", "-z"], cwd=root, check=True, capture_output=True
    )
    selected = []
    for name in result.stdout.decode("utf-8").split("\0"):
        if not name:
            continue
        relative = Path(name)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("Invalid tracked source path")
        if any(part in EXCLUDED_PARTS for part in relative.parts):
            continue
        allowed = name in TOP_LEVEL or (
            relative.parts[0] in SOURCE_DIRS
            and (relative.suffix in TEXT_SUFFIXES or relative.name == ".gitignore")
        )
        if not allowed:
            continue
        source = root / relative
        if source.is_symlink() or not source.resolve().is_relative_to(root.resolve()):
            raise ValueError(f"Source escapes repository: {name}")
        if not source.is_file():
            raise ValueError(f"Tracked source is missing: {name}")
        selected.append(relative)
    required = {
        "LICENSE",
        "README.md",
        "pyproject.toml",
        "bridge/mcp_dispatch.py",
        "bridge/dispatch_runner.py",
        "bridge/harness_courier/mcp_server.py",
    }
    if not required.issubset({path.as_posix() for path in selected}):
        raise ValueError("Required release files are missing from the Git index")
    return sorted(selected, key=lambda path: path.as_posix())


def checked_payloads(root: Path, selected: list[Path]) -> dict[Path, bytes]:
    payloads = {}
    findings = []
    for relative in selected:
        data = (root / relative).read_bytes()
        text = data.decode("utf-8-sig")
        for line_number, line in enumerate(text.splitlines(), 1):
            for category, pattern in PATTERNS.items():
                if pattern.search(line):
                    findings.append(f"{relative.as_posix()}:{line_number}: {category}")
        payloads[relative] = data
    if findings:
        # Report locations only, never print matched secrets or private text.
        raise ValueError("Release scan requires review:\n" + "\n".join(findings))
    return payloads


def export(root: Path, destination: Path) -> dict:
    root = root.resolve()
    destination = destination.resolve()
    archive = Path(str(destination) + ".zip")
    if destination.is_relative_to(root) or root.is_relative_to(destination):
        raise ValueError("Release directory must be separate from the repository")
    if destination.exists() or archive.exists():
        raise ValueError("Refusing to overwrite an existing release")
    payloads = checked_payloads(root, selected_files(root))
    destination.mkdir(parents=True, exist_ok=False)
    rows = []
    for relative, data in payloads.items():
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        rows.append(
            {"path": relative.as_posix(), "sha256": hashlib.sha256(data).hexdigest()}
        )
    manifest = {
        "project": "harness-courier",
        "version": "0.1.0-preview",
        "license": "MIT",
        "files": rows,
        "git_history_included": False,
        "third_party_binaries_included": False,
        "known_pattern_scan": "passed; not an exhaustive confidentiality guarantee",
    }
    (destination / "release-manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    with zipfile.ZipFile(archive, "x", compression=zipfile.ZIP_DEFLATED) as bundle:
        for relative in payloads:
            bundle.write(destination / relative, relative.as_posix())
        bundle.write(destination / "release-manifest.json", "release-manifest.json")
    return {
        "files": len(rows),
        "directory": str(destination),
        "archive": str(archive),
        "archive_sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(export(ROOT, args.output), indent=2))


if __name__ == "__main__":
    main()
