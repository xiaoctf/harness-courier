"""Fixed, finite-lifetime outbox worker; stdout is never an MCP stream."""

import argparse
from pathlib import Path

from dispatch_queue import run_worker

from bridge import Mailbox


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    args = parser.parse_args()
    run_worker(Mailbox(args.db))


if __name__ == "__main__":
    main()
