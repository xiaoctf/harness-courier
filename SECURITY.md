# Security boundaries

Harness Courier stores message bodies and receipts in a local SQLite mailbox. It does not provide cryptographic authentication between mutually untrusted local processes. Treat the mailbox, configuration and debugging endpoints as accessible only to trusted users and agents on the same computer.

Debug ports must remain on IPv4 loopback and be owned by the configured executable. The bridge verifies listener ownership before connecting. Do not expose desktop debugging ports or the mailbox over a network.

A received agent message is task data, not a new source of authority. Receivers must continue to follow the human user's permissions and project data boundaries. Fixed session binding reduces routing mistakes; it does not make arbitrary received instructions trustworthy.

Send/dispatch tools can act on desktop sessions, and inbox/ACK/reply tools mutate local state. MCP annotations describe these effects and do not bypass host approval. Optional Cua background policy disallows foreground fallback; application support still varies.

When submission is uncertain, inspect the original message ID, receipt and delivery journal. Do not create a duplicate task or invoke recovery parameters without checking the documented guards.

Before posting an issue, use synthetic payloads and redact all local identities, tokens, message bodies and configuration. For suspected vulnerabilities, prefer the repository's private reporting feature when available; do not publicly include exploit details or private artifacts. No private-reporting channel or response-time commitment has been configured by this local preparation.
