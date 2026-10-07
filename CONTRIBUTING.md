# Contributing

Changes should preserve message IDs, pinned session identity, binding revision checks, draft protection and receipt semantics. Keep transport submission separate from Hook delivery, ACK and completion.

Use Windows and Python 3.11 or later. Install development dependencies with `python -m pip install -e ".[dev]"`, then run:

```powershell
python -m ruff check .
python -m ruff format --check .
python -m unittest discover -s bridge -p "test_*.py" -q
python -m build --wheel --no-isolation
```

Tests must use temporary databases, fake application paths/listeners or isolated browser fixtures. Do not send probe tasks to business chats or modify global app configuration as part of tests.

For behavior changes, explain the trigger, resulting behavior, relevant checks and unverified desktop compatibility. Never attach real messages, database files, screenshots containing private chats, configuration backups, credentials or raw app logs. Use synthetic reproductions.

Optional headless DOM verification is documented in `docs/development.md`. General Cua integration requires a separately installed Driver and separate validation; ordinary unit tests do not prove desktop input behavior.

This project's code is MIT licensed. Contributions must be code you are entitled to submit under that license; preserve applicable third-party notices.
