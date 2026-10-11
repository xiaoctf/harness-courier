// ZCode 3.14.4: the mounted sidebar's onSelectTask(workspacePath, taskId).
// No OS focus, transcript reads, runtime creation, Stop, or text input.
async function(request) {
  const visible = e => !!e && e.getClientRects().length > 0;
  function composer() {
    const inputs = [...new Set([...document.querySelectorAll('[data-lexical-editor="true"][contenteditable="true"], [data-testid="chat-input"][contenteditable="true"]')].filter(visible))];
    if (inputs.length !== 1) return {error: 'composer_unavailable_or_ambiguous'};
    const editor = inputs[0], scope = editor.closest('[data-session-id]');
    if (!scope) return {error: 'session_identity_or_composer_mismatch'};
    if (editor.textContent !== '' || scope.querySelector('[data-media-att-id], [data-attachment-id], [data-composer-attachment-remove]')) return {error: 'existing_draft'};
    const busy = [...document.querySelectorAll('button.stop, button[data-testid="v4-stop"], button[data-testid="chat-stop-button"]')].some(b => visible(b) && !b.disabled && b.getAttribute('aria-disabled') !== 'true');
    if (busy && request.allow_busy_navigation !== true) return {error: 'current_conversation_generating'};
    return {editor, sid: scope.getAttribute('data-session-id')};
  }
  function currentRoot(editor) {
    const key = Object.keys(editor).find(k => k.startsWith('__reactFiber$'));
    let start = key && editor[key];
    for (let branch = 0; branch < 2 && start; branch++, start = start.alternate) {
      let service = null;
      for (let n = start, depth = 0; n && depth < 256; n = n.return, depth++) {
        if (n.tag === 10 && n.memoizedProps?.value?.windowControllerService) service = n.memoizedProps.value.windowControllerService;
        if (n.tag === 3) {
          if (n.stateNode?.current === n) return {root: n, service};
          break;
        }
      }
    }
    return null;
  }
  function selector(root) {
    const stack = [root], found = [], visited = new Set();
    while (stack.length && visited.size < 40000) {
      const node = stack.pop();
      if (visited.has(node)) return null;
      visited.add(node);
      if (node.child) stack.push(node.child);
      if (node.sibling) stack.push(node.sibling);
      const p = node.memoizedProps;
      // Exact current sidebar contract, not arbitrary task-row callbacks.
      if ([0, 14, 15].includes(node.tag) && p && typeof p.workspacePath === 'string' &&
          typeof p.onSelectTask === 'function' && typeof p.onStartDraftInWorkspace === 'function' &&
          Object.hasOwn(p, 'onCreateConversationTask') && Object.hasOwn(p, 'onOpenFolderFromWorkspaceMenu')) {
        if (p.workspaceRemoteSessionId) return null;
        found.push(p.onSelectTask);
      }
    }
    return stack.length === 0 && found.length === 1 ? found[0] : null;
  }
  if (typeof request.workspace_path !== 'string' || !/^[A-Za-z]:[\\/]/.test(request.workspace_path) ||
      typeof request.sid !== 'string' || !/^[A-Za-z0-9_.:-]{1,160}$/.test(request.sid)) return {error: 'native_navigation_identity_unavailable'};
  const before = composer();
  if (before.error) return before;
  const context = currentRoot(before.editor);
  const choose = context && selector(context.root);
  if (!choose || typeof context.service?.listTaskList !== 'function') return {error: 'native_navigation_adapter_unavailable'};
  let timer;
  try {
    const answer = await Promise.race([
      context.service.listTaskList({kind: 'timeline', workspaceScopes: [{workspacePath: request.workspace_path}], sortBy: 'updated'}),
      new Promise(resolve => { timer = setTimeout(() => resolve(null), 3000); }),
    ]);
    if (!answer || !Array.isArray(answer.items) || answer.hasMore !== false || answer.items.length > 10000) return {error: 'native_navigation_index_unavailable'};
    const targets = answer.items.filter(item => item.taskId === request.sid && item.workspacePath === request.workspace_path && !item.workspaceIdentity && !item.remoteSessionId);
    if (targets.length !== 1 || targets[0].sourceAvailability !== 'online') return {error: 'native_navigation_target_unverified'};
    // The metadata read can yield. Recheck live root, handler, current route,
    // drafts and busy authorization immediately before the one selection.
    const after = composer();
    if (after.error) return after;
    const fresh = currentRoot(after.editor);
    if (after.editor !== before.editor || after.sid !== before.sid ||
        fresh?.root !== context.root || fresh?.service !== context.service || selector(fresh.root) !== choose) return {error: 'native_navigation_context_changed'};
    choose(request.workspace_path, request.sid);
    return {selected: true, selection_method: 'native_exact_session'};
  } catch (_) {
    return {error: 'native_navigation_failed'};
  } finally {
    clearTimeout(timer);
  }
}
