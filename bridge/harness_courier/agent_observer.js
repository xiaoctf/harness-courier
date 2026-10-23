// Read-only session metadata. No navigation, focus, input, transcript or auth reads.
async function(request) {
  const visible = element => !!element && element.getClientRects().length > 0;
  const all = (selector, root = document) =>
    Array.from(root.querySelectorAll(selector)).filter(visible);
  const inputs = all(request.harness === 'kimi'
    ? '.ProseMirror[contenteditable="true"]'
    : '[data-lexical-editor="true"][contenteditable="true"], [data-testid="chat-input"][contenteditable="true"]');
  const unique = Array.from(new Set(inputs));
  const editor = unique.length === 1 ? unique[0] : null;
  const composer = request.harness === 'kimi' ? editor?.closest('.composer') : editor?.closest('[data-session-id]');
  const current = request.harness === 'kimi'
    ? location.pathname.match(/^\/sessions\/([A-Za-z0-9_-]+)(?:\/|$)/)?.[1]
    : composer?.getAttribute('data-session-id');
  const matches = current === request.sid;
  const scoped = matches && !!composer;
  const stops = scoped ? all('button.stop, button[data-testid="v4-stop"], button[data-testid="chat-stop-button"]', composer) : [];
  const sends = scoped ? all(request.harness === 'kimi' ? 'button.send' : '[data-testid="chat-send-button"], button[data-testid="v4-composer-send"], button[data-testid$="-send-button"]', composer) : [];
  const verified = scoped && (stops.length > 0 || sends.length === 1);
  let sidebar = {verified: false, turn_state: 'unknown'};
  if (request.harness === 'kimi') {
    // Kimi 1.0.4 active SessionRow semantics; absence alone never means idle.
    const rows = all('.se[data-session-id]').filter(row => row.getAttribute('data-session-id') === request.sid);
    const row = rows.length === 1 ? rows[0] : null;
    const known = row && all('.row .left > .t', row).length === 1 &&
      all('.act .ha button.pin-btn', row).length === 1 &&
      all('button.reopen-btn, button.restore-btn', row).length === 0;
    if (known) {
      const busy = all('.act .st .ui-spinner', row).length === 1;
      const idle = all('.act > .ts', row).length === 1 || all('.act .st .unread-dot', row).length === 1;
      if (busy !== idle) sidebar = {verified: true, turn_state: busy ? 'running' : 'idle'};
    }
  }

  let session = {verified: false, turn_state: 'unknown', reason: 'unsupported_harness'};
  if (request.harness === 'zcode') {
    session = await observeZCode();
  } else if (request.harness === 'kimi') {
    session = await observeKimi();
  }
  return {
    activity_probe: true, target_selected: matches, composer_verified: verified,
    sidebar, session_activity: session,
    generating: verified ? stops.some(button => !button.disabled && button.getAttribute('aria-disabled') !== 'true') : null,
    draft_present: verified ? !!editor.textContent || !!composer.querySelector('[data-media-att-id], [data-attachment-id], [data-composer-attachment-remove]') : null,
    native_queue_count: verified && request.harness === 'zcode' ? all('li[data-queue-item-id]', composer).length : null,
  };

  async function observeKimi() {
    const unknown = reason => ({verified: false, turn_state: 'unknown', reason});
    // Kimi Code 1.0.4's own client GET /sessions/:id returns current metadata.
    // No transcript, navigation, input, runtime creation or credential access.
    const deps = globalThis.__kimiAppClientDeps?.current;
    let api;
    try { api = deps?.api?.(); } catch (_) { return unknown('kimi_api_unavailable'); }
    if (typeof api?.getSession !== 'function') return unknown('kimi_api_unavailable');
    const controller = new AbortController();
    let timer;
    try {
      const value = await Promise.race([
        api.getSession(request.sid, {signal: controller.signal, background: true}),
        new Promise(resolve => { timer = setTimeout(() => { controller.abort(); resolve(null); }, 3000); }),
      ]);
      if (globalThis.__kimiAppClientDeps?.current !== deps || deps.api() !== api) return unknown('kimi_api_changed');
      if (value === null) return unknown('kimi_api_timeout');
      if (!value || value.id !== request.sid || typeof value.cwd !== 'string' || !/^[A-Za-z]:[\\/]/.test(value.cwd)) {
        return unknown('kimi_session_identity_unverified');
      }
      if (typeof value.busy !== 'boolean' || typeof value.mainTurnActive !== 'boolean' ||
          typeof value.archived !== 'boolean' || !['none', 'approval', 'question'].includes(value.pendingInteraction)) {
        return unknown('kimi_session_activity_unverified');
      }
      if ((!value.busy && (value.mainTurnActive || value.pendingInteraction !== 'none')) ||
          (value.archived && (value.busy || value.mainTurnActive || value.pendingInteraction !== 'none'))) {
        return unknown('native_activity_conflict');
      }
      const turn = value.archived ? 'session_closed_observed' :
        value.pendingInteraction === 'question' ? 'waiting_for_input' :
        value.pendingInteraction === 'approval' ? 'approval_requested' :
        value.mainTurnActive ? 'running' : 'idle';
      return {verified: true, session_id: value.id, workspace_path: value.cwd,
        turn_state: turn, source_availability: 'online', observation_source: 'kimi_session_api',
        archived: value.archived, has_background_work: value.busy && !value.mainTurnActive};
    } catch (error) {
      if (globalThis.__kimiAppClientDeps?.current !== deps) return unknown('kimi_api_changed');
      if (controller.signal.aborted) return unknown('kimi_api_timeout');
      if (error?.name === 'DaemonApiError' && error.code === 40401) {
        return {...unknown('target_not_found_in_kimi'), session_id: request.sid};
      }
      return unknown('kimi_api_query_failed');
    } finally {
      clearTimeout(timer);
    }
  }

  async function observeZCode() {
    const unknown = reason => ({verified: false, turn_state: 'unknown', reason});
    // Hook cwd is an execution directory, not necessarily the task workspace.
    // Resolve only the exact ID's native route; the controller must confirm it.
    function workspaceRoute() {
      const suffix = ':' + request.sid;
      const keys = all('li[data-task-item-key]').map(row => row.getAttribute('data-task-item-key'))
        .filter(key => key.endsWith(suffix));
      if (keys.length > 1) return {error: 'workspace_route_ambiguous'};
      return {path: keys.length === 1 ? keys[0].slice(0, -suffix.length) : request.workspace_path,
        source: keys.length === 1 ? 'native_sidebar_exact_session' : 'registered_cwd'};
    }
    const route = workspaceRoute();
    if (route.error) return unknown(route.error);
    const workspace = route.path;
    if (typeof workspace !== 'string' || !/^[A-Za-z]:[\\/]/.test(workspace)) {
      return unknown('workspace_identity_unavailable');
    }
    const workspaceEvidence = {registered_workspace_path: request.workspace_path, workspace_route: route.source};
    const routeChanged = () => {
      const fresh = workspaceRoute();
      return !!fresh.error || fresh.path !== workspace || fresh.source !== route.source;
    };
    // ZCode 3.14.4 exposes its window Host Controller through service context.
    // Only that one service is read; use its supported metadata-list query.
    // Task state comes from the live controller, never cached React task props.
    const seed = editor || all('li[data-task-item-key]')[0] || document.querySelector('#root > *');
    const fiberKey = seed && Object.keys(seed).find(key => key.startsWith('__reactFiber$'));
    let start = fiberKey ? seed[fiberKey] : null;
    let controller = null, agentService = null, currentRoot = null;
    for (let branch = 0; branch < 2 && start; branch++, start = start.alternate) {
      let node = start, candidate = null, candidateAgentService = null;
      for (let depth = 0; node && depth < 256; depth++, node = node.return) {
        if (node.tag === 10 && node.memoizedProps?.value?.windowControllerService) {
          candidate = node.memoizedProps.value.windowControllerService;
          candidateAgentService = node.memoizedProps.value.zcodeAgentService;
        }
        if (node.tag === 3) {
          if (node.stateNode?.current === node) {
            controller = candidate;
            agentService = candidateAgentService;
            currentRoot = node;
          }
          break;
        }
      }
      if (controller) break;
    }
    if (typeof controller?.listTaskList !== 'function') return unknown('controller_unavailable');
    let timer;
    const deadline = Date.now() + 3000;
    async function boundedQuery(promise) {
      clearTimeout(timer);
      return Promise.race([promise, new Promise(resolve => {
        timer = setTimeout(() => resolve(null), Math.max(0, deadline - Date.now()));
      })]);
    }
    try {
      // No search: search may inspect transcripts. No mutations or new runtimes.
      // Host source observation uses runtimePolicy=existing-only internally.
      const answer = await boundedQuery(controller.listTaskList({kind: 'timeline', workspaceScopes: [{workspacePath: workspace}], sortBy: 'updated'}));
      if (answer === null) return unknown('controller_timeout');
      if (currentRoot?.stateNode?.current !== currentRoot) return unknown('controller_root_changed');
      if (routeChanged()) return unknown('workspace_route_changed');
      if (!answer || !Array.isArray(answer.items) || answer.hasMore !== false || answer.items.length > 10000) {
        return unknown('controller_index_incomplete');
      }
      const items = answer.items.filter(item => item.taskId === request.sid && item.workspacePath === workspace &&
        !item.remoteSessionId && !item.workspaceIdentity);
      if (items.length === 0) return unknown('target_not_found_in_controller');
      if (items.length !== 1) return unknown('target_not_unique_in_controller');
      const item = items[0], activity = item.activity;
      if (item.sourceAvailability !== 'online') return unknown('target_source_offline');
      // Historical task status is not a liveness signal. A separately owned,
      // existing-runtime subscription provides the live overlay when absent.
      if (activity == null) return await observeRuntimeIndex();
      if (!activity || !Number.isFinite(activity.lastActivityAt) || activity.lastActivityAt < 0 ||
          activity.lastActivityAt > Date.now() + 5000 || typeof activity.hasBackgroundWork !== 'boolean') {
        return unknown('session_activity_schema_unverified');
      }
      const phases = {
        draft: ['idle', 'idle'], prewarming: ['running', 'running'], running: ['running', 'running'],
        completedSuccess: ['idle', 'completed'], completedInterrupted: ['idle', 'completed'], error: ['idle', 'error'],
      };
      const phase = Object.hasOwn(phases, activity.phase) ? phases[activity.phase] : null;
      if (!phase) return unknown('session_phase_unsupported');
      const pending = activity.pendingInteractions;
      if (pending && (!Number.isSafeInteger(pending.permissionCount) || pending.permissionCount < 0 ||
          !Number.isSafeInteger(pending.userInputCount) || pending.userInputCount < 0)) {
        return unknown('pending_interaction_schema_unverified');
      }
      let turn = phase[0];
      if (pending?.userInputCount > 0) turn = 'waiting_for_input';
      else if (pending?.permissionCount > 0) turn = 'approval_requested';
      if (turn === 'waiting_for_input' || turn === 'approval_requested') {
        if (!['running', 'waiting'].includes(item.liveStatus) || phase[0] !== 'running') return unknown('native_activity_conflict');
      } else if (item.liveStatus !== phase[1]) return unknown('native_activity_conflict');
      return {
        ...workspaceEvidence,
        verified: true, turn_state: turn, session_id: request.sid, workspace_path: workspace,
        source_availability: 'online', phase: activity.phase, last_activity_at: activity.lastActivityAt,
        has_background_work: activity.hasBackgroundWork,
        permission_count: pending?.permissionCount || 0, user_input_count: pending?.userInputCount || 0,
      };
    } catch (_) {
      return unknown('controller_query_failed');
    } finally {
      clearTimeout(timer);
    }

    async function observeRuntimeIndex() {
      if (typeof agentService?.subscribeSessionsIndexV4 !== 'function' ||
          typeof agentService?.unsubscribeSessionsIndexV4 !== 'function' ||
          typeof agentService?.onDynamicSessionsIndexFrame !== 'function' ||
          typeof crypto.randomUUID !== 'function') return unknown('runtime_index_unavailable');
      const topic = 'sessions-index/' + workspace;
      const subscriberScope = 'harness-courier-probe-' + crypto.randomUUID();
      let dispose, subscriptionId, closed = false, cleaned = false, wake;
      const frames = [];
      let signal = new Promise(resolve => { wake = resolve; });
      async function cleanup() {
        if (!subscriptionId || cleaned) return;
        cleaned = true;
        try {
          // Only our scoped observer is removed; never the desktop's observer.
          await agentService.unsubscribeSessionsIndexV4({workspacePath: workspace,
            runtimePolicy: 'existing-only', subscriptionId});
        } catch (_) { /* No private error text returned. */ }
      }
      try {
        const listener = agentService.onDynamicSessionsIndexFrame({workspacePath: workspace})(frame => {
          if (closed || frame?.topic !== topic) return;
          if (frames.length < 32) frames.push(frame);
          wake();
        });
        dispose = typeof listener === 'function' ? listener :
          typeof listener?.dispose === 'function' ? () => listener.dispose() : null;
        if (typeof dispose !== 'function') return unknown('runtime_index_listener_unverified');
        const subscribing = Promise.resolve(agentService.subscribeSessionsIndexV4({
          workspacePath: workspace, runtimePolicy: 'existing-only', subscriberScope,
        })).then(answer => {
          if (typeof answer?.ack?.subscriptionId === 'string' && answer.ack.subscriptionId) {
            subscriptionId = answer.ack.subscriptionId;
            if (closed) void cleanup(); // Late ACK must not leak our subscription.
          }
          return answer;
        });
        const answer = await boundedQuery(subscribing);
        if (answer === null) return unknown('runtime_index_timeout');
        const ack = answer?.ack;
        if (!subscriptionId || ack.mode !== 'snapshot' || typeof ack.logEpoch !== 'string' || !ack.logEpoch) {
          return unknown('runtime_index_ack_unverified');
        }
        let envelope;
        while (!envelope) {
          envelope = frames.find(frame => frame.subscriptionId === subscriptionId && frame.kind === 'complete');
          if (envelope) break;
          if (await boundedQuery(signal) === null) return unknown('runtime_index_timeout');
          signal = new Promise(resolve => { wake = resolve; });
        }
        if (currentRoot?.stateNode?.current !== currentRoot) return unknown('controller_root_changed');
        if (routeChanged()) return unknown('workspace_route_changed');
        const frame = envelope.frame, snapshot = frame?.payload?.snapshot;
        if (frame?.subscriptionId !== subscriptionId || frame.topic !== topic || frame.payload?.kind !== 'snapshot' ||
            snapshot?.protocolVersion !== 1 || snapshot.workspaceId !== workspace || snapshot.logEpoch !== ack.logEpoch ||
            !Array.isArray(snapshot.sessions) || snapshot.sessions.length > 10000) {
          return unknown('runtime_index_identity_unverified');
        }
        const sessions = snapshot.sessions.filter(value => value?.sessionId === request.sid);
        if (sessions.length !== 1 || sessions[0].workspaceId !== workspace) return unknown('runtime_index_target_not_unique');
        const live = sessions[0];
        const terminal = ['completedSuccess', 'completedInterrupted', 'error'].includes(live.phase);
        const states = {draft: 'idle', prewarming: 'running', running: 'running',
          completedSuccess: 'idle', completedInterrupted: 'idle', error: 'idle'};
        if (!Object.hasOwn(states, live.phase)) return unknown('session_phase_unsupported');
        if (typeof live.sessionEnded !== 'boolean' || live.sessionEnded !== terminal ||
            typeof live.hasBackgroundWork !== 'boolean' || !Number.isFinite(live.lastActivityAt) ||
            live.lastActivityAt < 0 || live.lastActivityAt > Date.now() + 5000) return unknown('session_activity_schema_unverified');
        const pending = live.pendingInteractionSummary;
        if (pending && (!Number.isSafeInteger(pending.permissionCount) || pending.permissionCount < 0 ||
            !Number.isSafeInteger(pending.userInputCount) || pending.userInputCount < 0)) return unknown('pending_interaction_schema_unverified');
        let turn = states[live.phase];
        if (pending?.userInputCount > 0) turn = 'waiting_for_input';
        else if (pending?.permissionCount > 0) turn = 'approval_requested';
        if (live.pendingInteraction != null) {
          const kind = live.pendingInteraction.kind;
          if (!['permission', 'userInput'].includes(kind) || !pending ||
              (kind === 'permission' ? pending.permissionCount : pending.userInputCount) < 1) return unknown('pending_interaction_schema_unverified');
        }
        if ((turn === 'waiting_for_input' || turn === 'approval_requested') && states[live.phase] !== 'running') {
          return unknown('native_activity_conflict');
        }
        return {...workspaceEvidence, verified: true, turn_state: turn, session_id: request.sid, workspace_path: workspace,
          source_availability: 'online', observation_source: 'sessions_index', phase: live.phase,
          last_activity_at: live.lastActivityAt, has_background_work: live.hasBackgroundWork,
          permission_count: pending?.permissionCount || 0, user_input_count: pending?.userInputCount || 0};
      } catch (error) {
        if (currentRoot?.stateNode?.current !== currentRoot) return unknown('controller_root_changed');
        if (routeChanged()) return unknown('workspace_route_changed');
        // The installed Host's existing-only path reports this fixed error
        // when the workspace has no runtime client. Do not start it to probe.
        const message = typeof error?.message === 'string' ? error.message : '';
        if (message.length <= 1000 && message.endsWith('ZCode Agent runtime is not running.')) {
          return {...unknown('runtime_not_running'), runtime_availability: 'not_running',
            session_id: request.sid, workspace_path: workspace, ...workspaceEvidence};
        }
        return unknown('runtime_index_query_failed');
      } finally {
        closed = true;
        if (typeof dispose === 'function') { try { dispose(); } catch (_) {} }
        // Cleanup is bounded separately. Late ACK cleanup remains attached.
        const removing = cleanup();
        let cleanupTimer;
        await Promise.race([removing, new Promise(resolve => { cleanupTimer = setTimeout(resolve, 500); })]);
        clearTimeout(cleanupTimer);
      }
    }
  }
}
