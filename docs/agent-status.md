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
Concurrent Hook processes can commit out of order. The latest locally observed
event and each event timestamp are monotonic: a delayed older Stop cannot replace
a newer prompt or progress event. This does not establish ordering inside the
harness before its Hook starts, or prove that independent jobs stopped.
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

Kimi Code 1.0.4 queries its current app client's `getSession(id)` metadata API
with a three-second deadline and cancellation. The response must match the exact
global session ID and provide boolean busy/main-turn/archive fields and a known
pending-interaction value. No prompt, title, credentials or transcript is returned.
`native_exact_session_api` supports hidden chats and distinguishes a main turn
from background work; it also reports pending approval and user input. An archived
target reports `session_closed_observed` with `bound_session_archived`. A backend
404 for the exact ID produces `bound_session_not_found`, never a guessed replacement.
These observations supersede old Hooks. The API is private and version-specific;
unsupported schemas remain unverified. `registration_workspace_mismatch` means a
Hook recorded a different cwd (sometimes the app launch directory); the adapter
does not rewrite that registration or discard the globally addressed observation.

Kimi sidebar fallback observations are grounded in the SessionRow implementation tested
on desktop 1.0.4: a running spinner, idle timestamp, or unread indicator has a
specific state meaning. The adapter verifies one exact visible session row and
its active-row structure; archived rows, duplicates, mixed indicators, rename
inputs and unsupported markup do not qualify. This supports background-chat
queries without selecting them. If the sidebar is collapsed or the target is
not rendered, this fallback cannot make that observation. Kimi Stop/progress Hooks
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

When the complete task index has no activity overlay, the adapter obtains a
fresh sessions-index snapshot through the same current service context. It uses
`zcodeAgentService.subscribeSessionsIndexV4` with `runtimePolicy=existing-only`
and a unique subscriber scope. It accepts only its own complete snapshot with
matching subscription, topic, workspace, epoch and exact session identity.
Historical `listSessions` status is never used: that interface can default to
`idle` without reading current activity. Malformed overlays are not replaced
with a fallback. The shared query deadline is three seconds; owned subscription
cleanup adds at most half a second to the probe. The listener and owned
subscription are removed, including a subscription whose ACK arrives after
timeout. Desktop subscriptions are left intact.

`native_exact_session_controller` identifies the task-overlay evidence;
`native_exact_session_index` identifies the owned runtime-index snapshot.

If the existing-only ZCode query reports no workspace runtime, `runtime_availability`
is `not_running`, `turn_state` remains `unknown`, and `attention` includes
`workspace_runtime_not_running`. A cached composer or old Hook cannot replace this
current evidence. Open the exact workspace/session before an authorized dispatch;
reconnecting Codex MCP alone does not start a ZCode workspace runtime. This says
nothing about detached training or other background processes. A missing target,
an archived chat and a missing workspace runtime require different recovery actions;
status queries never perform those actions.
`binding_peer_state` separately reports the last peer registration as
`last_seen_online`, `last_seen_closed` or `unregistered`. That registration
does not override current native activity. A closed registration without current
evidence prompts reopening the exact session or explicitly choosing a replacement;
the bridge never guesses a replacement from its title. An absent exact task
reports `target_not_found_in_controller`, distinct from an ambiguous task.

`waiting_for_input`,
`native_turn_error`, `native_source_offline` and `native_state_conflict` are
actionable attention signals; they do not create a result or approve anything.
The native `has_background_work` flag is separate from `background_jobs`: it
does not establish whether arbitrary detached OS processes are still running.

`background_jobs` is currently `unknown`: a main model turn can stop while a
training, server or detached process keeps running. Receiver completion is a
reported result; verify actual deliverables before declaring a parent goal done.

The read-only observer reconnects once after a WebSocket handshake or socket
timeout in its fixed activity query. Both stages share a two-attempt budget;
the retry uses the same verified renderer and exact recipient. Repeated failures
stay unavailable and report `probe_stage`, `connection_attempts`,
`observation_attempts` and the exception class without raw exception text.
Endpoint/identity failures and completed observations reporting offline,
unsupported schema or controller timeout are not retried. This policy applies
only to the read-only activity query. It never replays a task or desktop input;
delivery retains its separate receipt and uncertain-submission guards.

Sparse lifecycle Hook payloads preserve an already known nonempty title and
workspace. A supplied nonempty value updates that session's metadata; missing
metadata for a new session remains unknown. This prevents a later prompt or end
Hook from erasing the workspace needed by the exact ZCode controller lookup.

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

ZCode Hook `cwd` may be an execution subdirectory rather than the task workspace.
When the exact session ID has one visible native sidebar route, the adapter uses
that route only after the current controller confirms the same local task and
workspace. It rechecks the route after asynchronous reads; duplicates, changed
routes, remote identities and unverifiable sources remain guarded. The response
keeps `registered_workspace_path` and `workspace_route`; a confirmed difference
raises `registration_workspace_mismatch` without rewriting registrations or bindings.
If neither a usable exact route nor a correct registered workspace is available,
state remains unknown; no title matching, parent-directory guessing or GUI fallback
is performed by a status query.

Held/exhausted dispatches without receiver ACK appear in `blocked_dispatches` and
raise `dispatch_blocked`, even when the turn is idle or unknown. A wait on that
original message returns `attention_required` rather than silently timing out.
Receiver ACK/results supersede old dispatch failures.
