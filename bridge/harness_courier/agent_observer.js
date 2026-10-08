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
  }
  return {
    activity_probe: true, target_selected: matches, composer_verified: verified,
    sidebar, session_activity: session,
    generating: verified ? stops.some(button => !button.disabled && button.getAttribute('aria-disabled') !== 'true') : null,
    draft_present: verified ? !!editor.textContent || !!composer.querySelector('[data-media-att-id], [data-attachment-id], [data-composer-attachment-remove]') : null,
    native_queue_count: verified && request.harness === 'zcode' ? all('li[data-queue-item-id]', composer).length : null,
  };

  async function observeZCode() {
    const unknown = reason => ({verified: false, turn_state: 'unknown', reason});
    const workspace = request.workspace_path;
    if (typeof workspace !== 'string' || !/^[A-Za-z]:[\\/]/.test(workspace)) {
      return unknown('workspace_identity_unavailable');
    }
    // ZCode 3.14.4 exposes its window Host Controller through service context.
    // Only that one service is read; use its supported metadata-list query.
    // Task state comes from the live controller, never cached React task props.
    const seed = editor || all('li[data-task-item-key]')[0] || document.querySelector('#root > *');
    const fiberKey = seed && Object.keys(seed).find(key => key.startsWith('__reactFiber$'));
    let start = fiberKey ? seed[fiberKey] : null;
    let controller = null;
    for (let branch = 0; branch < 2 && start; branch++, start = start.alternate) {
      let node = start, candidate = null;
      for (let depth = 0; node && depth < 256; depth++, node = node.return) {
        if (node.tag === 10 && node.memoizedProps?.value?.windowControllerService) {
          candidate = node.memoizedProps.value.windowControllerService;
        }
        if (node.tag === 3) {
          if (node.stateNode?.current === node) controller = candidate;
          break;
        }
      }
      if (controller) break;
    }
    if (typeof controller?.listTaskList !== 'function') return unknown('controller_unavailable');
    let timer;
    try {
      // No search: search may inspect transcripts. No mutations or new runtimes.
      // Host source observation uses runtimePolicy=existing-only internally.
      const answer = await Promise.race([
        controller.listTaskList({kind: 'timeline', workspaceScopes: [{workspacePath: workspace}], sortBy: 'updated'}),
        new Promise(resolve => { timer = setTimeout(() => resolve(null), 3000); }),
      ]);
      if (answer === null) return unknown('controller_timeout');
      if (!answer || !Array.isArray(answer.items) || answer.hasMore !== false || answer.items.length > 10000) {
        return unknown('controller_index_incomplete');
      }
      const items = answer.items.filter(item => item.taskId === request.sid && item.workspacePath === workspace &&
        !item.remoteSessionId && !item.workspaceIdentity);
      if (items.length !== 1) return unknown('target_not_unique_in_controller');
      const item = items[0], activity = item.activity;
      if (item.sourceAvailability !== 'online') return unknown('target_source_offline');
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
  }
}
