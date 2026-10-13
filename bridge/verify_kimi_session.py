"""Kimi's metadata adapter in an isolated DOM with no external app requests."""

import json


def verify(client):
    def js(expression):
        return client.call(
            "Runtime.evaluate", {"expression": expression, "returnByValue": True}
        )["result"].get("value")

    binding = {"harness": "kimi", "session_id": "session_fixture"}
    js("""window.kimiApiFixture={saved:globalThis.__kimiAppClientDeps,calls:0,mode:'idle'};
const api={async getSession(sid,options){const f=window.kimiApiFixture;f.calls++;
 if(sid!=='session_fixture'||options.background!==true||!(options.signal instanceof AbortSignal)||
    Object.keys(options).length!==2)throw Error('unexpected read');
 if(f.mode==='timeout')return new Promise((_,reject)=>options.signal.addEventListener('abort',()=>reject(Error('aborted'))));
 if(f.mode==='missing')throw Object.assign(Error('PRIVATE'),{name:'DaemonApiError',code:40401});
 if(f.mode==='failure')throw Error('PRIVATE');
 if(f.mode==='changed')globalThis.__kimiAppClientDeps={current:{}};
 const value={id:sid,cwd:'E:/fixture',busy:false,mainTurnActive:false,pendingInteraction:'none',archived:false,
 title:'PRIVATE',lastPrompt:'SECRET'};
 if(['running','approval','question','background'].includes(f.mode))value.busy=true;
 if(['running','approval','question'].includes(f.mode))value.mainTurnActive=true;
 if(['approval','question'].includes(f.mode))value.pendingInteraction=f.mode;
 if(f.mode==='archived')value.archived=true;
 if(f.mode==='identity')value.id='other';
 if(f.mode==='workspace')value.cwd='relative';
 if(f.mode==='schema')delete value.mainTurnActive;
 if(f.mode==='conflict')value.mainTurnActive=true;
 return value;}};
window.kimiApiFixture.deps={current:{api:()=>api}};
globalThis.__kimiAppClientDeps=window.kimiApiFixture.deps;void 0""")
    try:
        cases = (
            ("idle", "idle"),
            ("running", "running"),
            ("background", "idle"),
            ("approval", "approval_requested"),
            ("question", "waiting_for_input"),
            ("archived", "session_closed_observed"),
            ("missing", "target_not_found_in_kimi"),
            ("identity", "kimi_session_identity_unverified"),
            ("workspace", "kimi_session_identity_unverified"),
            ("schema", "kimi_session_activity_unverified"),
            ("conflict", "native_activity_conflict"),
            ("changed", "kimi_api_changed"),
            ("failure", "kimi_api_query_failed"),
            ("timeout", "kimi_api_timeout"),
        )
        for mode, expected in cases:
            js(
                "globalThis.__kimiAppClientDeps=window.kimiApiFixture.deps;"
                "window.kimiApiFixture.mode=" + json.dumps(mode)
            )
            value = client.evaluate("agent_observe", binding)["session_activity"]
            assert value.get("reason", value["turn_state"]) == expected, (mode, value)
            assert "PRIVATE" not in json.dumps(value) and "SECRET" not in json.dumps(
                value
            )
            if value["verified"]:
                assert value["session_id"] == binding["session_id"]
                assert value["observation_source"] == "kimi_session_api"
            if mode == "background":
                assert value["has_background_work"] is True
        return {"scenarios": len(cases), "exact_metadata_only": True}
    finally:
        js(
            "globalThis.__kimiAppClientDeps=window.kimiApiFixture.saved;delete window.kimiApiFixture"
        )
