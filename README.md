# Harness Courier

**English** | [简体中文](README.zh-CN.md)

Send tasks from Codex to specific Kimi Code and ZCode desktop chats on Windows, then collect acknowledgements and results through MCP.

Harness Courier binds a readable alias to a real desktop session ID. Messages stay pinned to that session and binding revision, while a local SQLite mailbox tracks delivery, ACKs and results. An optional Cua Driver integration provides background computer input where the target application supports it.

[![Windows checks](https://github.com/xiaoctf/harness-courier/actions/workflows/ci.yml/badge.svg)](https://github.com/xiaoctf/harness-courier/actions/workflows/ci.yml)

[Download preview](https://github.com/xiaoctf/harness-courier/releases/tag/v0.1.0-preview) · [Quick start](#quick-start) · [MCP tools](#mcp-tools) · [Limitations](#limitations) · [Contributing](CONTRIBUTING.md)

## What it does

- **Bind a recipient:** address a chosen Kimi Code or ZCode chat by its real session ID, using an alias such as `projectA-kimi-review`.
- **Send and track:** store messages, bindings and receipts in a shared local SQLite mailbox.
- **Deliver in the background:** use a verified local CDP endpoint to submit recipient-scoped wake metadata. A receiver Hook or exact-ID receiver inbox call fetches the message body, including when Kimi steering skips a Hook.
- **Protect active work:** check the target session, drafts, attachments and busy state before sending. Preserve uncertain submission records to guard against duplicate sends.
- **Return a result:** receivers acknowledge the message and report success or failure through MCP. Codex queries the original message ID.
- **Observe agent activity:** read current turn state separately from receipts, including supported ZCode background-session metadata and Kimi sidebar observations.
- **Add optional computer control:** use the Cua background policy and registration helpers separately from message delivery.

This is a community integration for Windows desktop applications. Codex, Kimi Code, ZCode and Cua are independent products; their names do not imply official endorsement.

## How it works

```text
Codex --send to bound session--> SQLite mailbox
  |                                  |
  +--CDP wake marker--> Kimi / ZCode --receiver Hook--> fetch message
  |                                  |
  +--query original message ID-------+--MCP ACK / result--> mailbox
```

The stored message states are:

| State | Meaning |
| --- | --- |
| `queued` | The message is stored in the mailbox. |
| `delivered` | The receiver Hook or inbox tool has fetched it. |
| `acknowledged` | The receiving agent has confirmed reading it. |
| `completed` | The receiving agent has returned a successful result. |
| `failed` | The receiving agent has returned a failure result. |

Desktop `submitted` is a separate transport signal. It does not prove receipt or task completion. A `completed` receipt still needs the sender's review of the actual work.

## Quick start

### 1. Install from source

You need Windows, Python 3.11+, and installed Kimi Code / ZCode desktop applications. The Python dependencies are `psutil` and `websocket-client`.

```powershell
git clone https://github.com/xiaoctf/harness-courier.git
cd harness-courier
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
Copy-Item apps.example.toml apps.toml
```

You can also download the complete source ZIP from [Releases](https://github.com/xiaoctf/harness-courier/releases). It includes the documentation, CMD launchers and optional Cua integration. The wheel contains the Python service, Hooks and required resources. The package is not published to PyPI.

### 2. Set application paths

Edit `apps.toml` with your actual application executable paths. [apps.example.toml](apps.example.toml) shows the format. Defaults are generic `Program Files` examples; the launcher does not discover installations automatically.

`HARNESS_COURIER_APPS` can point to an alternate TOML file by absolute path. The old `HARNESS_BRIDGE_APPS` variable remains a fallback. Invalid fields, relative executable paths, duplicate ports and a missing explicitly selected file are rejected.

### 3. Register MCP and receiver Hooks

All three clients must share one mailbox. The installer registers MCP servers and Kimi/ZCode receiver Hooks while preserving unrelated settings.

```powershell
# Preview the installation plan.
.\.venv\Scripts\python.exe bridge\install_integrations.py

# After reviewing the plan, apply it.
.\.venv\Scripts\python.exe bridge\install_integrations.py --apply
```

A fresh installation uses the MCP registration name `harness_courier`. An existing owned `harness_bridge` registration is retained. Conflicting registrations are rejected. Reload the desktop clients after applying configuration so their sessions can run the receiver Hooks.

### 4. Start background delivery

```powershell
.\launcher\start_background.cmd --check
.\launcher\start_background.cmd --app all
```

`--check` does not launch or close applications. Exit code 0 means the check finished; inspect `background_ready` for readiness. If an application is already running without its debugging endpoint, save your work and close it before launching again. The launcher never forcibly closes applications, and starts new processes minimized without activating their windows.

The CMD launcher chooses `HARNESS_COURIER_PYTHON`, then the legacy `HARNESS_BRIDGE_PYTHON`, the repository `.venv`, `py -3`, or `python` on PATH. Set the environment variable to an absolute interpreter path when needed.

`--autostart` supports delayed startup and logging to `bridge/data/startup.log`; it does not create a Windows startup entry by itself.

### 5. Bind, send and receive

1. Trigger the receiver Hook in the intended Kimi/ZCode chat.
2. Use `courier_list_sessions` to find its real session ID. Confirm the project and recipient, then call `courier_bind_target` with a distinct alias.
3. Call `courier_send_message` with that alias and task body. Save the returned `id` for all later `message_id` arguments.
4. The receiving agent uses its Hook-provided `caller_session_id` to receive, acknowledge and return the result.
5. Codex uses `courier_wait_for_receipt` or `courier_get_message_status` to query that same message.

The message body is limited to 6,000 characters. Use aliases such as `<project>-<harness>-<role>` for different projects; this is a naming convention, not an access-isolation boundary. Chat titles alone are not session identity.

## MCP tools

| Tool | Available to | Purpose |
| --- | --- | --- |
| `courier_list_sessions` | All clients | List actual sessions and alias bindings. |
| `courier_bind_target` | Codex | Bind an alias to a chosen session. |
| `courier_send_message` | Codex | Create a message and optionally dispatch its wake. |
| `courier_dispatch_message` | Codex | Dispatch an existing message ID when safe to retry. |
| `courier_get_message_status` | All clients | Read state, ACK and result. |
| `courier_get_agent_status` | Codex | Read the bound session's current turn and recent progress without navigating or waking it. |
| `courier_wait_for_receipt` | All clients | Wait up to 55 seconds for ACK or a terminal result. |
| `courier_receive_messages` | Kimi / ZCode | Fetch messages for the caller's exact session. |
| `courier_acknowledge_message` | Kimi / ZCode | Confirm the message was read. |
| `courier_return_result` | Kimi / ZCode | Return a terminal success or failure result. |

Tool discovery also exposes the original `bridge_*` aliases with the same schemas and role restrictions. The old `harness_bridge` imports, CLI commands and environment variables remain supported.

For supervision, use `courier_get_agent_status(alias="project-kimi-review")`.
Codex receipt queries also include an `agent` observation for the message's
original recipient. `running`, `idle`, recent `stop_observed`,
`approval_requested`, `waiting_for_input`, and `unknown` describe different evidence. An idle agent
with unfinished receipts needs attention; idle never marks a message completed.
Queries do not switch chats or retry tasks. Supported ZCode versions expose exact-session
controller state even when the target chat is unselected or absent from the rendered
sidebar. An old Stop event does not invalidate that current observation.
See [Agent activity](docs/agent-status.md)
for Hook loading, freshness and background-job limits.

The MCP server runs a fixed background dispatcher internally, so agents do not need an extra shell command to send a wake. Host approvals still apply; tool annotations describe behavior and do not grant permissions.

For manual configuration, the source entry is:

```powershell
.\.venv\Scripts\python.exe bridge\bridge.py mcp --harness codex
```

After installation, `harness-courier mcp --harness codex` is the equivalent CLI entry. The default mailbox remains `bridge/data/bridge.sqlite3` in a source checkout. Keep installation and configuration paths valid when moving the repository.

## Optional Cua background control

[integrations/cua](integrations/cua/README.md) contains the policy, registration helper, MCP verifier and WPF test source for [Cua Driver](https://github.com/trycua/cua). The integration expects Driver version `0.33.3`; that is not a claim about the latest upstream version. No Driver binary is bundled.

Its MCP registration remains `cua_background`. Input actions should explicitly use `delivery_mode="background"`. An unavailable background action or policy denial must not trigger automatic foreground fallback. Support depends on the application, control and action.

Cua computer input and the Courier CDP message transport are separate integrations.

## Agent instructions and documentation

The following detailed guides and prompt templates are currently in Chinese:

- [Collaboration workflow](docs/agent-workflow.md): binding, task design, receipts and review.
- [Codex controller instructions](docs/prompts/codex-controller.md).
- [Kimi/ZCode receiver instructions](docs/prompts/kimi-zcode-receiver.md).
- [Task card template](docs/prompts/task-card.md).
- [Codex approval behavior](docs/codex-approvals.md).
- [Naming and compatibility](docs/naming-and-compatibility.md).
- [Development](docs/development.md) and [release preparation](docs/release.md).

Adapt the templates to the project and place them in the client's supported instruction files. MCP does not load these texts automatically or grant authority to send tasks.

## Limitations

`0.1.0` is a preview. The current source has 185 regression tests. Windows CI is configured for Python 3.11 and 3.13, Ruff and package builds; see [Actions](https://github.com/xiaoctf/harness-courier/actions) for each commit's actual result. Automated tests and isolated browser fixtures do not establish compatibility with every desktop application version.

- Receipts are stored in message records and queried by the sender. There is no complete scheduler that automatically wakes the original Codex chat with a result.
- Explicit dispatch requests use a durable outbox and one delivery worker per mailbox. Priority jobs go first; jobs of the same priority use FIFO order when ready. Transient pre-input failures have bounded retries, while drafts and uncertain submissions are held for inspection. Real multi-project desktop concurrency still needs acceptance testing.
- Local desktop tests with Kimi Code 1.0.4 and ZCode 3.14.4 verified real receipts, native priority consumption and activity observations from one originating Codex environment. Other versions, simultaneous independent Codex senders, Cua input and host approval behavior still need acceptance testing in target environments.

Messages, databases, logs, local configuration, backups and third-party binaries are excluded from the source distribution.

## Development and license

Install development tools with `python -m pip install -e ".[dev]"`. See [CONTRIBUTING.md](CONTRIBUTING.md) for checks and [SECURITY.md](SECURITY.md) for security reporting.

Harness Courier's own code is [MIT licensed](LICENSE). External dependencies retain their own licenses; see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

The current source adds a durable outbox, bounded retries, lock waiting, busy-chat navigation, native priority delivery and an optional administrator policy that forces priority. Recipient-scoped wake metadata also supports exact-ID inbox retrieval when native steering skips a receiver Hook. Dispatch acceptance is asynchronous: inspect `dispatch_queue`, the original message ID's receipts and current agent activity. Local desktop validation is bounded to the versions above. The earlier `v0.1.0-preview` release archive does not include these upgrades; use the current `main` source. See [dispatch and priority](docs/dispatch-and-priority.md) (Chinese).

Dispatch defaults to native priority and busy-chat navigation. Set `priority=false` to queue normally only when the mailbox administrator has not enabled `force_priority`. With that policy enabled, enqueue and delivery override `false`; existing draft and identity guards remain in force.
