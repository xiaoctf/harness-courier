// Bounded renderer operations: composer/routing metadata only, never transcripts.
// Every mutation verifies the actual session and current composer again.
function(request) {
  const visible = element => !!element && element.getClientRects().length > 0;
  const all = (selector, root = document) =>
    Array.from(root.querySelectorAll(selector)).filter(visible);
  const inputs = all(request.harness === 'kimi'
    ? '.ProseMirror[contenteditable="true"]'
    : '[data-lexical-editor="true"][contenteditable="true"], [data-testid="chat-input"][contenteditable="true"]');
  const unique = Array.from(new Set(inputs));
  const editor = unique.length === 1 ? unique[0] : null;

  let sessionId = null;
  if (request.harness === 'kimi') {
    const match = location.pathname.match(/^\/sessions\/([A-Za-z0-9_-]+)(?:\/|$)/);
    if (match) sessionId = match[1];
  } else if (editor) {
    const root = editor.closest('[data-session-id]');
    if (root) sessionId = root.getAttribute('data-session-id');
  }
  const draft = editor ? (editor.textContent || '') : null;
  const composer = editor ? (request.harness === 'kimi'
    ? editor.closest('.composer')
    : editor.closest('.chat-composer-input-surface') || editor.closest('[data-session-id]')) : null;
  const hasAttachments = !!composer?.querySelector(
    '[data-media-att-id], [data-attachment-id], [data-composer-attachment-remove]');
  const matches = sessionId === request.sid;
  if (request.op === 'agent_observe') {
    // Metadata only; never focus/select, inspect transcript text, or expose
    // the unrelated current conversation ID or draft contents.
    const root = request.harness === 'kimi' ? composer : editor?.closest('[data-session-id]');
    const scoped = matches && unique.length === 1 && !!root;
    const stopButtons = scoped ? all('button.stop, button[data-testid="v4-stop"], button[data-testid="chat-stop-button"]', root) : [];
    const sendButtons = scoped ? all(request.harness === 'kimi' ? 'button.send' : '[data-testid="chat-send-button"], button[data-testid="v4-composer-send"], button[data-testid$="-send-button"]', root) : [];
    const verified = scoped && (stopButtons.length > 0 || sendButtons.length === 1);
    // Kimi 1.0.4's SessionRow renders .ts only for idle, and a spinner only
    // for running. Exact visible IDs allow observing a background chat without
    // selecting it. Require the known active-row structure; ambiguous, renamed,
    // archived or changed markup remains unknown rather than false idle.
    let sidebar = {verified: false, turn_state: 'unknown'};
    if (request.harness === 'kimi') {
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
    return {
      activity_probe: true, target_selected: matches, composer_verified: verified,
      sidebar,
      generating: verified ? stopButtons.some(button => !button.disabled && button.getAttribute('aria-disabled') !== 'true') : null,
      draft_present: verified ? draft !== '' || hasAttachments : null,
      native_queue_count: verified && request.harness === 'zcode' ? all('li[data-queue-item-id]', root).length : null,
    };
  }
  const lineEquivalent = (left, right) =>
    typeof left === 'string' && typeof right === 'string' &&
    left.replace(/[\r\n]/g, '') === right.replace(/[\r\n]/g, '');
  const payloadMatches = draft === request.marker ||
    (request.marker.includes('\n') && lineEquivalent(draft, request.marker));
  const result = {
    matches, sid: sessionId, editor_count: unique.length,
    draft_empty: draft === '', marker_present: payloadMatches,
  };
  const stops = all('button.stop, button[data-testid="v4-stop"], button[data-testid="chat-stop-button"]');
  result.generating = stops.some(button => !button.disabled && button.getAttribute('aria-disabled') !== 'true');
  if (request.op === 'observe') return result;

  if (request.op === 'select' || request.op === 'select_authorized_busy_navigation') {
    if (!editor) return {...result, error: 'composer_unavailable_or_ambiguous'};
    if (draft !== '' || hasAttachments) return {...result, error: 'existing_draft'};
    // A normal delivery must not switch away from a chat that is generating.
    if (request.op === 'select' && stops.some(button =>
      !button.disabled && button.getAttribute('aria-disabled') !== 'true')) {
      return {...result, error: 'current_conversation_generating'};
    }
    const rows = request.harness === 'kimi'
      ? all('[data-session-id]').filter(element =>
          element.getAttribute('data-session-id') === request.sid && element.classList.contains('se'))
      : all('li[data-task-item-key]').filter(element =>
          (element.getAttribute('data-task-item-key') || '').endsWith(':' + request.sid));
    if (rows.length !== 1) return {...result, error: 'bound_chat_not_unique_or_not_visible'};
    rows[0].click();
    return {selected: true};
  }

  if (!matches || !editor) return {...result, error: 'session_identity_or_composer_mismatch'};
  if (!composer) return {...result, error: 'composer_scope_unverified'};
  if (hasAttachments) return {...result, error: 'existing_attachment_draft'};
  if (request.op === 'priority_queue_ready' || request.op === 'priority_queue_submit') {
    if (request.harness !== 'zcode') return {...result, error: 'priority_queue_unsupported'};
    if (draft !== '') return {...result, error: 'existing_draft'};
    // Queue metadata only. Never search transcript text or choose a queue index.
    const root = editor.closest('[data-session-id]');
    const rows = all('li[data-queue-item-id]', root).filter(row =>
      row.querySelector('span[title]')?.getAttribute('title') === request.marker);
    if (rows.length > 1) return {...result, error: 'priority_queue_ambiguous'};
    if (rows.length === 0) return {...result, ready: false};
    const row = rows[0];
    const buttons = all('button[data-testid^="v4-queue-item-send-now"]', row).filter(button =>
      button.getAttribute('data-queue-item-id') === row.getAttribute('data-queue-item-id'));
    if (row.getAttribute('data-dispatch-state') !== 'queued' || buttons.length !== 1 ||
        buttons[0].disabled || buttons[0].getAttribute('aria-disabled') === 'true') {
      return {...result, error: 'priority_queue_not_actionable'};
    }
    if (request.op === 'priority_queue_submit') {
      buttons[0].click();
      return {...result, priority_requested: true};
    }
    return {...result, ready: true};
  }
  if (request.op === 'focus') {
    if (draft !== '') return {...result, error: 'existing_draft'};
    // Document focus only; never BrowserWindow.focus or Page.bringToFront.
    editor.focus({preventScroll: true});
    return {...result, focused: document.activeElement === editor};
  }

  if (['ready', 'submit', 'keyboard_submit_ready'].includes(request.op)) {
    if (!payloadMatches) return {...result, error: 'marker_or_draft_changed'};
    const root = request.harness === 'kimi' ? composer : editor.closest('[data-session-id]');
    const buttons = all(request.harness === 'kimi'
      ? 'button.send'
      : '[data-testid="chat-send-button"], button[data-testid="v4-composer-send"], button[data-testid$="-send-button"]', root);
    if (buttons.length !== 1 || buttons[0].disabled ||
        buttons[0].getAttribute('aria-disabled') === 'true') {
      return {...result, error: 'send_button_unavailable'};
    }
    if (request.op === 'keyboard_submit_ready') {
      if (document.activeElement !== editor) return {...result, error: 'composer_focus_changed'};
      return {...result, ready: true};
    }
    if (request.op === 'submit') {
      buttons[0].click();
      return {submitted: true, sid: sessionId};
    }
    return {...result, ready: true};
  }
  return {...result, error: 'unknown_operation'};
}
