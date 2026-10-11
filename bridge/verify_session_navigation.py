"""Native navigation guard scenarios for verify_cdp_fixture's isolated browser."""

from session_navigation import select_zcode_session


def verify(client):
    def js(expression):
        return client.call(
            "Runtime.evaluate", {"expression": expression, "returnByValue": True}
        )["result"].get("value")

    binding = {
        "harness": "zcode",
        "session_id": "sess_fixture",
        "workspace_path": "E:/fixture",
    }
    js(
        """window.navFixture={child:fixtureRoot.child,list:controller.listTaskList,chosen:[],props:{workspacePath:'E:/current',onStartDraftInWorkspace(){},onCreateConversationTask(){},onOpenFolderFromWorkspaceMenu(){},onSelectTask(workspace,sid){window.navFixture.chosen.push([workspace,sid]);editor.closest('[data-session-id]').setAttribute('data-session-id',sid)}}};void 0"""
    )

    def reset():
        js(
            """window.navFixture.chosen=[];fixtureRoot.stateNode.current=fixtureRoot;fixtureRoot.child={tag:15,memoizedProps:window.navFixture.props};controller.listTaskList=window.navFixture.list;window.fixtureControllerMode='normal';window.fixtureNativeItem.sourceAvailability='online';window.fixtureNativeItem.taskId='sess_fixture';window.fixtureNativeItem.workspacePath='E:/fixture';delete window.fixtureNativeItem.workspaceIdentity;delete window.fixtureNativeItem.remoteSessionId;editor.textContent='';editor.closest('[data-session-id]').setAttribute('data-session-id','different_fixture');document.querySelector('[data-nav-fixture-attachment]')?.remove();document.querySelector('[data-nav-fixture-stop]')?.remove();void 0"""
        )

    cases = [
        ("editor.textContent='KEEP_DRAFT'", "existing_draft"),
        (
            "let a=document.createElement('span');a.dataset.attachmentId='fixture';a.setAttribute('data-nav-fixture-attachment','');editor.parentElement.appendChild(a)",
            "existing_draft",
        ),
        (
            "window.fixtureNativeItem.sourceAvailability='offline'",
            "native_navigation_target_unverified",
        ),
        (
            "window.fixtureNativeItem.taskId='wrong'",
            "native_navigation_target_unverified",
        ),
        (
            "window.fixtureNativeItem.workspacePath='E:/other'",
            "native_navigation_target_unverified",
        ),
        (
            "window.fixtureNativeItem.remoteSessionId='remote'",
            "native_navigation_target_unverified",
        ),
        (
            "window.fixtureNativeItem.workspaceIdentity='remote'",
            "native_navigation_target_unverified",
        ),
        (
            "window.fixtureControllerMode='duplicate'",
            "native_navigation_target_unverified",
        ),
        (
            "window.fixtureControllerMode='truncated'",
            "native_navigation_index_unavailable",
        ),
        (
            "window.fixtureControllerMode='timeout'",
            "native_navigation_index_unavailable",
        ),
        (
            "fixtureRoot.child.sibling={tag:15,memoizedProps:window.navFixture.props}",
            "native_navigation_adapter_unavailable",
        ),
        ("fixtureRoot.stateNode.current={}", "native_navigation_adapter_unavailable"),
        (
            "controller.listTaskList=async q=>{const r=await window.navFixture.list(q);editor.textContent='NEW_DRAFT';return r}",
            "existing_draft",
        ),
        (
            "controller.listTaskList=async q=>{const r=await window.navFixture.list(q);editor.closest('[data-session-id]').setAttribute('data-session-id','changed');return r}",
            "native_navigation_context_changed",
        ),
        (
            "controller.listTaskList=async q=>{const r=await window.navFixture.list(q);fixtureRoot.stateNode.current={};return r}",
            "native_navigation_context_changed",
        ),
    ]
    try:
        for setup, expected in cases:
            reset()
            js("(()=>{" + setup + ";})()")
            result = select_zcode_session(client, binding, allow_busy_navigation=True)
            assert result.get("error") == expected, (expected, result)
            assert js("window.navFixture.chosen.length") == 0
        reset()
        js(
            "(()=>{let b=document.createElement('button');b.className='stop';b.setAttribute('data-nav-fixture-stop','');document.body.appendChild(b)})()"
        )
        result = select_zcode_session(client, binding, allow_busy_navigation=False)
        assert result.get("error") == "current_conversation_generating"
        assert js("window.navFixture.chosen.length") == 0
        result = select_zcode_session(client, binding, allow_busy_navigation=True)
        assert result.get("selected") is True
        assert js("window.navFixture.chosen") == [["E:/fixture", "sess_fixture"]]
        assert js("editor.textContent") == ""
        return {"rejected_scenarios": len(cases) + 1, "verified_selection_once": True}
    finally:
        reset()
        js(
            "fixtureRoot.child=window.navFixture.child;controller.listTaskList=window.navFixture.list;editor.closest('[data-session-id]').setAttribute('data-session-id','sess_fixture');void 0"
        )
