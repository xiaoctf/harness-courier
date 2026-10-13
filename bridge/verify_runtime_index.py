"""Exercise owned live-index subscriptions in an isolated DOM, never a user app."""


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
    js("""window.indexFixture={activity:window.fixtureNativeItem.activity,
service:fixtureProvider.memoizedProps.value.zcodeAgentService,listeners:new Set(),
active:new Set(['desktop-owned']),scopes:new Set(),queries:0,mode:'normal',phase:'completedSuccess'};
fixtureProvider.memoizedProps.value.zcodeAgentService={
 onDynamicSessionsIndexFrame(query){if(query.workspacePath!=='E:/fixture')throw Error('workspace');
  return listener=>{window.indexFixture.listeners.add(listener);const dispose=()=>window.indexFixture.listeners.delete(listener);
   return window.indexFixture.mode==='functionDispose'?dispose:{dispose};};},
 async subscribeSessionsIndexV4(query){
  const f=window.indexFixture,mode=f.mode;f.queries++;
  if(query.workspacePath!=='E:/fixture'||query.runtimePolicy!=='existing-only'||
     !query.subscriberScope.startsWith('harness-courier-probe-')||f.scopes.has(query.subscriberScope)||
     Object.keys(query).length!==3)throw Error('unsafe subscription');
  f.scopes.add(query.subscriberScope);
  if(mode==='reject')throw Error('PRIVATE');
  if(mode==='runtimeAbsent')throw Error('ZCode Agent runtime is not running.');
  if(mode==='runtimeAbsentWrapped')throw Error('Error invoking remote method: Error: ZCode Agent runtime is not running.');
  if(mode==='lateAck')await new Promise(r=>setTimeout(r,3200));
  const id='fixture-'+f.queries,topic='sessions-index/E:/fixture';f.active.add(id);
  let session={sessionId:'sess_fixture',workspaceId:'E:/fixture',phase:f.phase,
    sessionEnded:['completedSuccess','completedInterrupted','error'].includes(f.phase),
    hasBackgroundWork:false,lastActivityAt:Date.now()-600000};
  if(mode==='sid')session.sessionId='other';if(mode==='workspace')session.workspaceId='E:/other';
  if(mode==='ended')session.sessionEnded=!session.sessionEnded;
  if(mode==='time')session.lastActivityAt=Date.now()+60000;if(mode==='background')delete session.hasBackgroundWork;
  if(mode==='pending')session.pendingInteractionSummary={userInputCount:-1,permissionCount:0};
  if(mode==='input')session.pendingInteractionSummary={userInputCount:1,permissionCount:0};
  if(mode==='permission')session.pendingInteractionSummary={userInputCount:0,permissionCount:1};
  if(mode==='pendingKind')session.pendingInteraction={kind:'other'};
  const snapshot={protocolVersion:1,workspaceId:'E:/fixture',logEpoch:'epoch',
    sessions:mode==='none'?[]:mode==='duplicate'?[session,session]:[session]};
  if(mode==='version')snapshot.protocolVersion=2;if(mode==='epoch')snapshot.logEpoch='old';
  if(mode==='snapshotWorkspace')snapshot.workspaceId='E:/other';
  const envelope={kind:'complete',topic,subscriptionId:id,frame:{topic,subscriptionId:id,
    payload:{kind:'snapshot',snapshot}}};
  if(mode==='subid')envelope.frame.subscriptionId='desktop-owned';
  if(mode==='topic')envelope.frame.topic='sessions-index/E:/other';
  if(mode==='fragment')envelope.kind='fragment';if(mode==='payload')envelope.frame.payload.kind='delta';
  if(mode==='root')fixtureRoot.stateNode.current=null;
  const emit=()=>{for(const listener of f.listeners){listener({...envelope,subscriptionId:'desktop-owned'});listener(envelope)}};
  if(mode!=='noFrame'){if(mode==='afterAck')setTimeout(emit,20);else emit();}
  return {ack:{subscriptionId:id,mode:mode==='resume'?'resume':'snapshot',logEpoch:'epoch'}};
 },
 async unsubscribeSessionsIndexV4(query){const f=window.indexFixture;
  if(query.workspacePath!=='E:/fixture'||query.runtimePolicy!=='existing-only'||
     query.subscriptionId==='desktop-owned'||!f.active.delete(query.subscriptionId))throw Error('unsafe cleanup');
 }};delete window.fixtureNativeItem.activity;void 0""")

    def reset(mode="normal", phase="completedSuccess"):
        import json

        js(
            """fixtureRoot.stateNode.current=fixtureRoot;window.fixtureControllerMode='normal';
window.fixtureNativeItem.sourceAvailability='online';window.fixtureNativeItem.liveStatus='completed';
delete window.fixtureNativeItem.activity;window.indexFixture.mode="""
            + json.dumps(mode)
            + ";window.indexFixture.phase="
            + json.dumps(phase)
            + ";void 0"
        )

    def clean():
        value = js("""({listeners:window.indexFixture.listeners.size,
active:Array.from(window.indexFixture.active)})""")
        assert value == {"listeners": 0, "active": ["desktop-owned"]}, value

    cases = (
        ("none", "runtime_index_target_not_unique"),
        ("duplicate", "runtime_index_target_not_unique"),
        ("sid", "runtime_index_target_not_unique"),
        ("workspace", "runtime_index_target_not_unique"),
        ("snapshotWorkspace", "runtime_index_identity_unverified"),
        ("version", "runtime_index_identity_unverified"),
        ("epoch", "runtime_index_identity_unverified"),
        ("subid", "runtime_index_identity_unverified"),
        ("topic", "runtime_index_identity_unverified"),
        ("payload", "runtime_index_identity_unverified"),
        ("ended", "session_activity_schema_unverified"),
        ("time", "session_activity_schema_unverified"),
        ("background", "session_activity_schema_unverified"),
        ("pending", "pending_interaction_schema_unverified"),
        ("pendingKind", "pending_interaction_schema_unverified"),
        ("input", "native_activity_conflict"),
        ("root", "controller_root_changed"),
        ("reject", "runtime_index_query_failed"),
        ("runtimeAbsent", "runtime_not_running"),
        ("runtimeAbsentWrapped", "runtime_not_running"),
        ("resume", "runtime_index_ack_unverified"),
        ("noFrame", "runtime_index_timeout"),
        ("fragment", "runtime_index_timeout"),
        ("lateAck", "runtime_index_timeout"),
    )
    try:
        for phase, turn in (
            ("draft", "idle"),
            ("prewarming", "running"),
            ("running", "running"),
            ("completedSuccess", "idle"),
            ("completedInterrupted", "idle"),
            ("error", "idle"),
        ):
            reset(phase=phase)
            value = client.evaluate("agent_observe", binding)["session_activity"]
            assert value["verified"] and value["turn_state"] == turn, value
            assert value["observation_source"] == "sessions_index"
            assert value["has_background_work"] is False
            clean()
        for mode, turn in (
            ("input", "waiting_for_input"),
            ("permission", "approval_requested"),
            ("afterAck", "running"),
            ("functionDispose", "running"),
        ):
            reset(mode, "running")
            value = client.evaluate("agent_observe", binding)["session_activity"]
            assert value["verified"] and value["turn_state"] == turn, value
            clean()
        reset(phase="unsupported")
        value = client.evaluate("agent_observe", binding)["session_activity"]
        assert value["reason"] == "session_phase_unsupported", value
        clean()
        for mode, reason in cases:
            reset(mode)
            value = client.evaluate("agent_observe", binding)["session_activity"]
            assert not value["verified"] and value["reason"] == reason, (mode, value)
            if reason == "runtime_not_running":
                assert value["runtime_availability"] == "not_running", value
                assert value["session_id"] == binding["session_id"], value
                assert value["workspace_path"] == binding["workspace_path"], value
            if mode == "lateAck":
                client.call(
                    "Runtime.evaluate",
                    {
                        "expression": "new Promise(r=>setTimeout(r,400))",
                        "awaitPromise": True,
                    },
                )
            clean()
        for expression, reason in (
            (
                "window.fixtureNativeItem.activity={}",
                "session_activity_schema_unverified",
            ),
            (
                "window.fixtureNativeItem.sourceAvailability='offline'",
                "target_source_offline",
            ),
        ):
            reset()
            js(expression)
            calls = js("window.indexFixture.queries")
            value = client.evaluate("agent_observe", binding)["session_activity"]
            assert value["reason"] == reason, value
            assert js("window.indexFixture.queries") == calls
            clean()
        return {
            "rejected_scenarios": len(cases) + 3,
            "live_phases": 6,
            "owned_cleanup": True,
            "pending_and_ack_order": True,
        }
    finally:
        reset()
        js("""window.fixtureNativeItem.activity=window.indexFixture.activity;
fixtureProvider.memoizedProps.value.zcodeAgentService=window.indexFixture.service;void 0""")
