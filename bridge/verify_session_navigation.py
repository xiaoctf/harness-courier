"""Native navigation guard scenarios for verify_cdp_fixture's isolated browser."""

from agent_activity import probe_native
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
        # Hook cwd can be a nested execution directory, while the native row
        # still belongs to the root workspace. Query and select only its ID.
        reset()
        js("document.querySelector('li[data-task-item-key]').style.display=''")
        nested = {**binding, "workspace_path": "E:/fixture/runs/nested"}
        observed = probe_native(client, nested)["session_activity"]
        assert observed["verified"] and observed["workspace_path"] == "E:/fixture", (
            observed
        )
        assert observed["registered_workspace_path"] == nested["workspace_path"]
        assert observed["workspace_route"] == "native_sidebar_exact_session"
        assert js("window.navFixture.chosen.length") == 0
        assert (
            js("editor.closest('[data-session-id]').getAttribute('data-session-id')")
            == "different_fixture"
        )
        result = select_zcode_session(client, nested, allow_busy_navigation=True)
        assert result.get("selected") is True, result
        assert js("window.navFixture.chosen") == [["E:/fixture", "sess_fixture"]]
        for setup, expected in (
            (
                "let row=document.querySelector('li[data-task-item-key]').cloneNode(true);row.setAttribute('data-task-item-key','E:/other:sess_fixture');row.dataset.routeDuplicate='';document.body.appendChild(row)",
                "native_navigation_workspace_ambiguous",
            ),
            (
                "controller.listTaskList=async q=>{const r=await window.navFixture.list(q);document.querySelector('li[data-task-item-key]').setAttribute('data-task-item-key','E:/other:sess_fixture');return r}",
                "native_navigation_context_changed",
            ),
        ):
            reset()
            js("(()=>{" + setup + ";})()")
            observed = probe_native(client, nested)["session_activity"]
            expected_reason = (
                "workspace_route_ambiguous"
                if expected == "native_navigation_workspace_ambiguous"
                else "workspace_route_changed"
            )
            assert observed.get("reason") == expected_reason, observed
            assert js("window.navFixture.chosen.length") == 0
            js(
                "document.querySelector('[data-route-duplicate]')?.remove();document.querySelector('li[data-task-item-key]').setAttribute('data-task-item-key','E:/fixture:sess_fixture')"
            )
            reset()
            js("(()=>{" + setup + ";})()")
            result = select_zcode_session(client, nested, allow_busy_navigation=True)
            assert result.get("error") == expected, result
            assert js("window.navFixture.chosen.length") == 0
            js(
                "document.querySelector('[data-route-duplicate]')?.remove();document.querySelector('li[data-task-item-key]').setAttribute('data-task-item-key','E:/fixture:sess_fixture')"
            )
        return {
            "rejected_scenarios": len(cases) + 3,
            "verified_selection_once": True,
            "hook_cwd_mismatch_resolved_without_input": True,
        }
    finally:
        js(
            "document.querySelector('[data-route-duplicate]')?.remove();document.querySelector('li[data-task-item-key]').setAttribute('data-task-item-key','E:/fixture:sess_fixture')"
        )
        reset()
        js(
            "fixtureRoot.child=window.navFixture.child;controller.listTaskList=window.navFixture.list;editor.closest('[data-session-id]').setAttribute('data-session-id','sess_fixture');void 0"
        )
