# Agent activity and task receipts

Use `courier_get_agent_status(alias="project-kimi-review")` to inspect a bound
desktop session. `bridge_agent_status` is its legacy alias. The controller-only
tool is read-only and idempotent: it does not navigate, type, focus, deliver,
interrupt, approve, or restart an agent.

```json
{"alias":"project-kimi-review","observe_native":true,"stale_after_seconds":300}
```

`observe_native=false` skips the desktop read. The freshness threshold accepts
finite numbers from 30 to 86400 seconds. It is an attention threshold, not a
deadline or proof that a process has stalled.

| `turn_state` | Evidence and meaning |
| --- | --- |
| `running` | The exact selected session has a verified composer with an enabled native Stop control, a verified exact Kimi sidebar row reports running, or ZCode's live controller reports the exact session running/prewarming. |
| `idle` | The exact selected session has verified native controls and no enabled Stop control, a verified exact Kimi sidebar row reports idle, or ZCode's live controller reports the exact session draft/completed/error with consistent live status. An error is also reported in `attention`. |
| `stop_observed` | A recent native Stop Hook was received. It is a pre-end observation; another Hook may continue the turn. |
| `approval_requested` | A recent PermissionRequest Hook was received, or ZCode's exact live session reports pending permissions. This observer makes no approval decision. |
| `waiting_for_input` | ZCode's exact live session reports pending user input. This does not mean task completion and never triggers an automatic answer. |
| `session_closed_observed` | A recent supported SessionEnd Hook was received. It does not prove background jobs exited. |
| `unknown` | No verified native observation or fresh event establishes a state. An offline source, conflicting native states, changed schemas or unverifiable endpoints stay unknown. Silence is not a stopped signal. |

Fresh exact native observations take precedence over historical Stop events.
PermissionRequest remains a separate attention signal while a turn is active.
For ZCode, a live controller's pending interaction counts supersede an old
PermissionRequest: an already answered request must not leave the agent stuck
in `approval_requested`.
`last_hook`, event/progress ages, and their revision expose evidence freshness.
This observer retains only event names, session identifiers and local observation
times; it does not retain tool arguments, transcript paths, assistant messages,
or permission payloads. These identifiers are local user data and must not be
published as example production configuration.

`attention` contains `idle_with_unfinished_messages` when delivered or ACKed
messages remain without terminal results while the agent is idle. It may also
contain `approval_requested` or `progress_needs_verification`. Tool-free thinking
or a long blocking tool may produce no progress events. Investigate the original
message and actual work before deciding what action is authorized. These flags
never trigger retries or restarts.

A recent Stop Hook with unfinished delivered/ACKed receipts produces
`stop_observed_with_unfinished_messages`. This requests investigation without
claiming the agent is definitely idle or marking any task complete.

Kimi sidebar observations are grounded in the SessionRow implementation tested
on desktop 1.0.4: a running spinner, idle timestamp, or unread indicator has a
specific state meaning. The adapter verifies one exact visible session row and
its active-row structure; archived rows, duplicates, mixed indicators, rename
inputs and unsupported markup do not qualify. This supports background-chat
queries without selecting them. If the sidebar is collapsed or the target is
not rendered, the probe cannot make that observation. Kimi Stop/progress Hooks
are optional evidence: this local desktop/backend combination did not emit
them during the live test, so the adapter does not depend on them.

ZCode queries use the window Host Controller's workspace-scoped `listTaskList`
metadata interface, grounded in desktop 3.14.4. The registered receiver's exact
session ID and workspace must match one local, online task. The query does not
select a chat, expand a sidebar, search transcripts, launch a runtime or send a
prompt. Its internal source observer uses `runtimePolicy=existing-only`.
Controller activity describes the current session even if its last activity
timestamp or Stop Hook is old: those times describe a state change, while this
query supplies a new observation. An unselected or unrendered target therefore
does not itself cause `unknown`.

The adapter obtains only the window controller service from a mounted React
service context anchored to the current root; it never trusts cached React task
props. It bounds the controller wait to three seconds, requires a complete index
and checks source availability, identity, phase and pending-interaction schemas.
`native.session_activity` contains the resulting metadata or a specific reason
such as `target_source_offline`, `controller_unavailable`, `controller_timeout`
or `target_not_unique_in_controller`. Unsupported desktop versions may change
this private service binding and must be revalidated. Offline or conflicting
state is reported for investigation rather than replaced by a stale Stop Hook.

`native_exact_session_controller` identifies this evidence. `waiting_for_input`,
`native_turn_error`, `native_source_offline` and `native_state_conflict` are
actionable attention signals; they do not create a result or approve anything.
The native `has_background_work` flag is separate from `background_jobs`: it
does not establish whether arbitrary detached OS processes are still running.

`background_jobs` is currently `unknown`: a main model turn can stop while a
training, server or detached process keeps running. Receiver completion is a
reported result; verify actual deliverables before declaring a parent goal done.

Controller message-status calls include an `agent` observation for the message's
frozen recipient, even if the alias has since been rebound. Wait calls inspect
passive observations and, when at least 20 seconds remain, also read the exact
native session. Native reads are spaced five seconds apart; short waits use
stored Hooks. `wait_reason=receipt` means the requested receiver state arrived,
`attention_required` reports an idle/Stop receipt gap or an observed approval
request or pending native user input, and `timeout` means the bound expired.
A terminal receipt can arrive
while the agent is still finishing its turn; query agent status if actual turn
termination is required.

`submitted_without_receiver_confirmation` distinguishes a desktop submission
older than ten seconds from a confirmed receiver fetch. When that session is
idle, `idle_with_submitted_unreceived_messages` explicitly reports the gap.
Intentionally queued messages sent with `dispatch=false` do not qualify.

Kimi native steering can bypass UserPromptSubmit. Its wake envelope therefore
contains only the exact recipient/message identifiers and instructions to fetch
that ID through the receiver MCP. The receiver Hook accepts legacy markers and
the exact scoped envelope, rejects changed or mismatched envelopes, and retains
the same mailbox recipient and terminal replay guards. A desktop submission
still does not prove the model consumed it.

## Installation and existing sessions

The installer appends owned `Stop`, `PostToolUse`, `PostToolUseFailure` and
`PermissionRequest` Hooks, preserves unrelated Hooks, and leaves them unchanged
on repeated installs. Passive observers return empty output and do not register
peers, inject context, claim messages or mark results.

Reload the Codex MCP connection to discover the new tool. External desktop
sessions may cache their Hook settings at creation: create a test session or
resume/reopen a business session at a safe boundary to load the new observers.
Selecting an existing chat alone is not proof that its Hook configuration reloaded.
Do not interrupt a healthy job solely to load observers. Native read-only probes
can already observe a selected old session; missing Hook events remain explicit.
ZCode's controller query also works on unselected old sessions without reloading
their Hooks. Installing this observer's Python entry requires one normal Codex
MCP reconnect; subsequent changes to the native observer asset are read on each
query instead of cached with the delivery adapter. Desktop apps need no restart.

This feature does not automatically wake the sending Codex chat. Use supported
bounded waits or explicitly authorized scheduling for later supervision.
An already configured Codex heartbeat can check every thirty minutes: query the
original message IDs and their frozen recipient observations, verify terminal
deliverables, investigate idle receipt gaps, and otherwise remain quiet. Never
turn a heartbeat into a duplicate dispatch of an ACKed task.

Hook semantics: [Kimi Hooks](https://www.kimi.com/code/docs/en/kimi-code-cli/customization/hooks.html),
[ZCode Hooks](https://zcode.z.ai/en/docs/hooks).
