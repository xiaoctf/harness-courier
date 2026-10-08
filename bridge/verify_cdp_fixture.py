"""Live CDP/DOM preflight in an isolated headless browser, never a user profile.

This verifies the protocol and guard implementation, not Kimi/ZCode compatibility.
"""

import contextlib
import ctypes
import json
import socket
import subprocess
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import cdp_delivery as cdp
import psutil
from wake_protocol import wake_text

ROOT = Path(__file__).resolve().parent
CHROME = Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe")


def line_equivalent(value):
    """Contenteditable insertText may render newlines as element boundaries."""
    return value.replace("\r", "").replace("\n", "")


class Point(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


def desktop_state():
    # Passive observer only; no Windows input or UI Automation calls.
    p = Point()
    ctypes.windll.user32.GetCursorPos(ctypes.byref(p))
    return {
        "foreground": int(ctypes.windll.user32.GetForegroundWindow()),
        "cursor": [p.x, p.y],
    }


HTML = b"""<!doctype html><meta charset="utf-8"><title>Harness background fixture</title>
<div class="se" data-session-id="session_fixture"><div class="row"><div class="left"><span class="t">Fixture</span></div><span class="act"><span class="ts">now</span><span class="ha"><button class="pin-btn">Pin</button></span></span></div></div>
<li data-task-item-key="E:/fixture:sess_fixture">Fixture</li>
<div data-session-id="sess_fixture" class="composer">
 <div class="ProseMirror" data-lexical-editor="true" data-testid="chat-input" contenteditable="true"></div>
 <button class="send" data-testid="v4-composer-send" disabled>Send fixture</button>
</div><script>
 const editor=document.querySelector('[contenteditable]'), send=document.querySelector('button.send');
 window.fixtureNativeItem={taskId:'sess_fixture',workspacePath:'E:/fixture',sourceAvailability:'online',liveStatus:'completed',activity:{phase:'completedSuccess',lastActivityAt:Date.now()-600000,hasBackgroundWork:false}};
 window.fixtureControllerMode='normal';
 const controller={async listTaskList(query){
  if(query.kind!=='timeline'||query.sortBy!=='updated'||query.workspaceScopes.length!==1||query.workspaceScopes[0].workspacePath!=='E:/fixture'||'search' in query)throw Error('unexpected query');
  if(window.fixtureControllerMode==='reject')throw Error('private error');
  if(window.fixtureControllerMode==='timeout')return new Promise(()=>{});
  return {items:window.fixtureControllerMode==='duplicate'?[window.fixtureNativeItem,window.fixtureNativeItem]:[window.fixtureNativeItem],hasMore:window.fixtureControllerMode==='truncated'};
 }};
 const fixtureRoot={tag:3};fixtureRoot.stateNode={current:fixtureRoot};
 const fixtureProvider={tag:10,memoizedProps:{value:{windowControllerService:controller}},return:fixtureRoot};
 editor.__reactFiber$fixture={tag:5,return:fixtureProvider};
 window.fixtureSubmitted=[];
 editor.addEventListener('input',()=>{send.disabled=!editor.textContent.trim();});
 send.addEventListener('click',()=>{window.fixtureSubmitted.push(editor.textContent);editor.textContent='';send.disabled=true;});
 window.fixtureSteered=[];window.fixturePromoted=[];window.fixtureMode='normal';
 function queueWake(text,id){
  const row=document.createElement('li');row.dataset.queueItemId=id;row.dataset.dispatchState='queued';
  const span=document.createElement('span');span.title=text;span.textContent=text;row.appendChild(span);
  const button=document.createElement('button');button.dataset.testid='v4-queue-item-send-now:'+id;button.dataset.queueItemId=id;
  button.addEventListener('click',()=>{window.fixturePromoted.push(text);row.remove();});row.appendChild(button);document.querySelector('.composer').appendChild(row);
 }
 editor.addEventListener('keydown',e=>{if(e.key==='Enter'){e.preventDefault();if(e.ctrlKey){if(window.fixtureMode!=='kimi-idle'){window.fixtureSteered.push(editor.textContent);editor.textContent='';send.disabled=true;}}else send.click();}});
 send.addEventListener('click',()=>{if(window.fixtureMode==='queue')queueWake(window.fixtureSubmitted.at(-1),'new-wake');});
 document.querySelector('.se').addEventListener('click',()=>history.pushState({},'', '/sessions/session_fixture'));
 document.querySelector('li').addEventListener('click',()=>document.querySelector('.composer').setAttribute('data-session-id','sess_fixture'));
</script>"""


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.startswith("/sessions/"):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(HTML)
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, *args):
        pass


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def main():
    temp_root = ROOT / "test-tmp"
    temp_root.mkdir(exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="cdp-", dir=temp_root))
    assert work.resolve().is_relative_to(temp_root.resolve())
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    port = free_port()
    url = f"http://127.0.0.1:{server.server_port}/sessions/session_fixture"
    proc = subprocess.Popen(
        [
            str(CHROME),
            "--headless=new",
            f"--user-data-dir={work / 'profile'}",
            f"--remote-debugging-port={port}",
            "--remote-debugging-address=127.0.0.1",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-background-networking",
            "--disable-component-update",
            url,
        ],
        creationflags=subprocess.CREATE_NO_WINDOW,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    from urllib.request import ProxyHandler, build_opener

    opener = build_opener(ProxyHandler({}))

    class FixtureClient(cdp.CdpClient):
        def __init__(self, harness):
            self.harness = harness
            self.port = port
            self.socket = None
            self.sequence = 0
            self.opener = opener

        def targets(self):
            with opener.open(
                f"http://127.0.0.1:{port}/json/list", timeout=2
            ) as response:
                data = json.load(response)
            return [t for t in data if t.get("type") == "page" and t.get("url") == url]

    try:
        deadline = time.monotonic() + 12
        while True:
            try:
                client = FixtureClient("kimi")
                targets = client.targets()
                if len(targets) == 1:
                    break
            except Exception:
                if time.monotonic() >= deadline:
                    raise
            time.sleep(0.15)
        listeners = [
            c
            for c in psutil.net_connections(kind="tcp")
            if c.status == psutil.CONN_LISTEN and c.laddr.port == port
        ]
        owned = {proc.pid} | {
            p.pid for p in psutil.Process(proc.pid).children(recursive=True)
        }
        assert listeners and all(
            c.laddr.ip == "127.0.0.1" and c.pid in owned for c in listeners
        )
        client.attach(targets[0])
        results = []
        deadline = time.monotonic() + 5
        while True:
            initial = client.evaluate(
                "observe",
                {
                    "harness": "kimi",
                    "session_id": "session_fixture",
                    "title": "Fixture",
                },
            )
            if initial.get("matches") and initial.get("editor_count") == 1:
                break
            if time.monotonic() >= deadline:
                raise RuntimeError("Fixture DOM not ready: " + json.dumps(initial))
            time.sleep(0.1)
        with (
            patch.object(cdp, "ROOT", work),
            patch("desktop_delivery.desktop_lock", contextlib.nullcontext),
        ):
            read_expression = "JSON.stringify({html:document.body.innerHTML,focus:document.activeElement.tagName,url:location.href})"
            for harness, sid in [
                ("kimi", "session_fixture"),
                ("zcode", "sess_fixture"),
            ]:
                binding = {"harness": harness, "session_id": sid, "title": "Fixture"}
                if harness == "zcode":
                    client.call(
                        "Runtime.evaluate",
                        {
                            "expression": "document.querySelector('.composer').setAttribute('data-session-id','sess_other')",
                            "returnByValue": True,
                        },
                    )
                    native_binding = {**binding, "workspace_path": "E:/fixture"}
                    client.call(
                        "Runtime.evaluate",
                        {
                            "expression": "document.querySelector('.composer').setAttribute('data-session-id','sess_other');document.querySelector('li[data-task-item-key]').style.display='none'",
                            "returnByValue": True,
                        },
                    )
                    native_baseline = client.call(
                        "Runtime.evaluate",
                        {"expression": read_expression, "returnByValue": True},
                    )
                    native = client.evaluate("agent_observe", native_binding)
                    assert (
                        not native["target_selected"]
                        and native["session_activity"]["verified"]
                    )
                    assert native["session_activity"]["turn_state"] == "idle"
                    assert (
                        native["session_activity"]["last_activity_at"]
                        < time.time() * 1000 - 300000
                    )
                    assert (
                        client.call(
                            "Runtime.evaluate",
                            {"expression": read_expression, "returnByValue": True},
                        )
                        == native_baseline
                    )
                    # Live controller metadata works without selecting/rendering
                    # the target; unread flags and historical React task props
                    # are deliberately irrelevant to these queries.
                    for phase, live, expected in (
                        ("draft", "idle", "idle"),
                        ("prewarming", "running", "running"),
                        ("running", "running", "running"),
                        ("completedSuccess", "completed", "idle"),
                        ("completedInterrupted", "completed", "idle"),
                        ("error", "error", "idle"),
                    ):
                        expression = (
                            "Object.assign(window.fixtureNativeItem,{liveStatus:"
                            + json.dumps(live)
                            + ",unreadAt:Date.now()});window.fixtureNativeItem.activity.phase="
                            + json.dumps(phase)
                        )
                        client.call(
                            "Runtime.evaluate",
                            {"expression": expression, "returnByValue": True},
                        )
                        assert (
                            client.evaluate("agent_observe", native_binding)[
                                "session_activity"
                            ]["turn_state"]
                            == expected
                        )
                    for pending, expected in (
                        (
                            {"permissionCount": 1, "userInputCount": 0},
                            "approval_requested",
                        ),
                        (
                            {"permissionCount": 0, "userInputCount": 2},
                            "waiting_for_input",
                        ),
                    ):
                        expression = (
                            "window.fixtureNativeItem.liveStatus='waiting';Object.assign(window.fixtureNativeItem.activity,{phase:'running',hasBackgroundWork:true,pendingInteractions:"
                            + json.dumps(pending)
                            + "})"
                        )
                        client.call(
                            "Runtime.evaluate",
                            {"expression": expression, "returnByValue": True},
                        )
                        native = client.evaluate("agent_observe", native_binding)[
                            "session_activity"
                        ]
                        assert (
                            native["turn_state"] == expected
                            and native["has_background_work"]
                        )
                    client.call(
                        "Runtime.evaluate",
                        {
                            "expression": "delete window.fixtureNativeItem.activity.pendingInteractions;window.fixtureNativeItem.liveStatus='completed';window.fixtureNativeItem.activity.phase='completedSuccess'",
                            "returnByValue": True,
                        },
                    )
                    for expression, reason in (
                        (
                            "window.fixtureNativeItem.sourceAvailability='offline'",
                            "target_source_offline",
                        ),
                        (
                            "window.fixtureNativeItem.sourceAvailability='online';window.fixtureNativeItem.liveStatus='running'",
                            "native_activity_conflict",
                        ),
                        (
                            "window.fixtureNativeItem.liveStatus='completed';window.fixtureNativeItem.taskId='wrong'",
                            "target_not_unique_in_controller",
                        ),
                        (
                            "window.fixtureNativeItem.taskId='sess_fixture';window.fixtureNativeItem.workspacePath='E:/other'",
                            "target_not_unique_in_controller",
                        ),
                        (
                            "window.fixtureNativeItem.workspacePath='E:/fixture';window.fixtureNativeItem.remoteSessionId='remote'",
                            "target_not_unique_in_controller",
                        ),
                        (
                            "delete window.fixtureNativeItem.remoteSessionId;window.fixtureNativeItem.activity.phase='future_phase'",
                            "session_phase_unsupported",
                        ),
                        (
                            "window.fixtureNativeItem.activity.phase='completedSuccess';window.fixtureControllerMode='duplicate'",
                            "target_not_unique_in_controller",
                        ),
                        (
                            "window.fixtureControllerMode='truncated'",
                            "controller_index_incomplete",
                        ),
                        (
                            "window.fixtureControllerMode='reject'",
                            "controller_query_failed",
                        ),
                        (
                            "window.fixtureControllerMode='timeout'",
                            "controller_timeout",
                        ),
                    ):
                        client.call(
                            "Runtime.evaluate",
                            {"expression": expression, "returnByValue": True},
                        )
                        native = client.evaluate("agent_observe", native_binding)[
                            "session_activity"
                        ]
                        assert not native["verified"] and native["reason"] == reason, (
                            native
                        )
                    # A stale React branch cannot supply the controller. Follow
                    # its alternate only when anchored in the current HostRoot.
                    client.call(
                        "Runtime.evaluate",
                        {
                            "expression": "window.fixtureControllerMode='normal';const currentFiber=editor.__reactFiber$fixture;editor.__reactFiber$fixture={tag:5,return:{tag:3,stateNode:{current:null}},alternate:currentFiber};void 0",
                            "returnByValue": True,
                        },
                    )
                    assert client.evaluate("agent_observe", native_binding)[
                        "session_activity"
                    ]["verified"]
                    client.call(
                        "Runtime.evaluate",
                        {
                            "expression": "editor.__reactFiber$fixture.alternate=null",
                            "returnByValue": True,
                        },
                    )
                    assert (
                        client.evaluate("agent_observe", native_binding)[
                            "session_activity"
                        ]["reason"]
                        == "controller_unavailable"
                    )
                    client.call(
                        "Runtime.evaluate",
                        {
                            "expression": "editor.__reactFiber$fixture=currentFiber;document.querySelector('li[data-task-item-key]').style.display='';document.querySelector('.composer').setAttribute('data-session-id','sess_fixture')",
                            "returnByValue": True,
                        },
                    )
                    assert client.evaluate("select", binding).get("selected")
                    assert client.evaluate("observe", binding).get("matches")
                before = desktop_state()
                # A read-only activity observation must preserve DOM, focus and
                # physical desktop state; a different chat must stay unknown.
                read_expression = "JSON.stringify({html:document.body.innerHTML,focus:document.activeElement.tagName,url:location.href})"
                baseline = client.call(
                    "Runtime.evaluate",
                    {"expression": read_expression, "returnByValue": True},
                )
                activity = client.evaluate("agent_observe", binding)
                assert activity["target_selected"] and activity["composer_verified"]
                assert activity["generating"] is False
                other = client.evaluate(
                    "agent_observe", {**binding, "session_id": "other-chat"}
                )
                assert not other["target_selected"] and other["generating"] is None
                assert other["sidebar"]["verified"] is False
                assert (
                    client.call(
                        "Runtime.evaluate",
                        {"expression": read_expression, "returnByValue": True},
                    )
                    == baseline
                )
                # Stop controls count only inside the exact composer.
                client.call(
                    "Runtime.evaluate",
                    {
                        "expression": "{const activityStop=document.createElement('button');activityStop.className='stop';activityStop.id='activity-stop';document.querySelector('.composer').appendChild(activityStop)}",
                        "returnByValue": True,
                    },
                )
                assert client.evaluate("agent_observe", binding)["generating"] is True
                client.call(
                    "Runtime.evaluate",
                    {
                        "expression": "document.querySelector('#activity-stop').remove();document.querySelector('button.send').removeAttribute('data-testid');document.querySelector('button.send').className='unrecognized'",
                        "returnByValue": True,
                    },
                )
                unverified = client.evaluate("agent_observe", binding)
                assert (
                    not unverified["composer_verified"]
                    and unverified["generating"] is None
                )
                client.call(
                    "Runtime.evaluate",
                    {
                        "expression": "document.querySelector('button.unrecognized').className='send';document.querySelector('button.send').dataset.testid='v4-composer-send'",
                        "returnByValue": True,
                    },
                )
                # The user/other agents may move the desktop concurrently.
                # DOM/focus equality above is attributable to this read; a
                # physical cursor comparison over time is only an observation.
                if harness == "kimi":
                    client.call(
                        "Runtime.evaluate",
                        {
                            "expression": "history.pushState({},'', '/sessions/other-chat')",
                            "returnByValue": True,
                        },
                    )
                    side = client.evaluate("agent_observe", binding)
                    assert not side["target_selected"] and side["sidebar"] == {
                        "verified": True,
                        "turn_state": "idle",
                    }
                    client.call(
                        "Runtime.evaluate",
                        {
                            "expression": "document.querySelector('.act > .ts').outerHTML='<span class=st><span class=ui-spinner></span></span>'",
                            "returnByValue": True,
                        },
                    )
                    assert client.evaluate("agent_observe", binding)["sidebar"] == {
                        "verified": True,
                        "turn_state": "running",
                    }
                    client.call(
                        "Runtime.evaluate",
                        {
                            "expression": "document.querySelector('.act').insertAdjacentHTML('afterbegin','<span class=ts>ambiguous</span>')",
                            "returnByValue": True,
                        },
                    )
                    assert not client.evaluate("agent_observe", binding)["sidebar"][
                        "verified"
                    ]
                    client.call(
                        "Runtime.evaluate",
                        {
                            "expression": "document.querySelector('.act .st').remove();document.querySelector('.ha button').className='reopen-btn'",
                            "returnByValue": True,
                        },
                    )
                    assert not client.evaluate("agent_observe", binding)["sidebar"][
                        "verified"
                    ]
                    client.call(
                        "Runtime.evaluate",
                        {
                            "expression": "document.querySelector('.ha button').className='pin-btn';history.pushState({},'', '/sessions/session_fixture')",
                            "returnByValue": True,
                        },
                    )
                mid = "msg_" + ("a" if harness == "kimi" else "b") * 32
                delivered = cdp.deliver_background(binding, mid, FixtureClient)
                actual = client.call(
                    "Runtime.evaluate",
                    {
                        "expression": "JSON.stringify(window.fixtureSubmitted)",
                        "returnByValue": True,
                    },
                )
                submitted = json.loads(actual["result"]["value"])
                after = desktop_state()
                assert line_equivalent(wake_text(mid, harness, sid)) in list(
                    map(line_equivalent, submitted)
                )
                assert delivered["submitted"] and not delivered["receiver_verified"]
                results.append(
                    {
                        "harness_fixture": harness,
                        "actual_marker_received": True,
                        "result": delivered,
                        "cursor_preserved": before["cursor"] == after["cursor"],
                        "foreground_preserved": before["foreground"]
                        == after["foreground"],
                    }
                )
            # Exercise native priority paths and exact queue identity in the DOM fixture.
            client.call(
                "Runtime.evaluate",
                {
                    "expression": "const busyStop=document.createElement('button');busyStop.className='stop';document.body.appendChild(busyStop);queueWake('OLD_QUEUE_KEEP','old-queue');window.fixtureMode='normal'",
                    "returnByValue": True,
                },
            )
            kimi_binding = {
                "harness": "kimi",
                "session_id": "session_fixture",
                "title": "Fixture",
            }
            kimi_mid = "msg_" + "c" * 32
            priority_kimi = cdp.deliver_background(
                kimi_binding, kimi_mid, FixtureClient, priority=True
            )
            client.call(
                "Runtime.evaluate",
                {"expression": "window.fixtureMode='queue'", "returnByValue": True},
            )
            zcode_binding = {
                "harness": "zcode",
                "session_id": "sess_fixture",
                "title": "Fixture",
            }
            zcode_mid = "msg_" + "d" * 32
            priority_zcode = cdp.deliver_background(
                zcode_binding, zcode_mid, FixtureClient, priority=True
            )
            actual = client.call(
                "Runtime.evaluate",
                {
                    "expression": "JSON.stringify({steered:window.fixtureSteered,promoted:window.fixturePromoted,old:!!document.querySelector('li[data-queue-item-id=old-queue]')})",
                    "returnByValue": True,
                },
            )
            priority_observed = json.loads(actual["result"]["value"])
            priority_observed["steered"] = list(
                map(line_equivalent, priority_observed["steered"])
            )
            assert priority_observed == {
                "steered": [
                    line_equivalent(wake_text(kimi_mid, "kimi", "session_fixture"))
                ],
                "promoted": [f"[HARNESS_BRIDGE_WAKE:{zcode_mid}]"],
                "old": True,
            }
            assert priority_kimi["priority_action"] == "kimi_steer_current_draft"
            assert priority_zcode["priority_action"] == "zcode_send_queued_now"
            # A stale/false busy probe must not downgrade native priority.
            client.call(
                "Runtime.evaluate",
                {
                    "expression": "document.querySelector('button.stop').remove();window.fixtureMode='normal'",
                    "returnByValue": True,
                },
            )
            false_busy_kimi_mid = "msg_" + "f" * 32
            false_busy_kimi = cdp.deliver_background(
                kimi_binding, false_busy_kimi_mid, FixtureClient, priority=True
            )
            assert false_busy_kimi["priority_action"] == "kimi_priority_shortcut"
            client.call(
                "Runtime.evaluate",
                {"expression": "window.fixtureMode='queue'", "returnByValue": True},
            )
            false_busy_zcode_mid = "msg_" + "1" * 32
            false_busy_zcode = cdp.deliver_background(
                zcode_binding, false_busy_zcode_mid, FixtureClient, priority=True
            )
            assert false_busy_zcode["priority_action"] == "zcode_send_queued_now"
            client.call(
                "Runtime.evaluate",
                {"expression": "window.fixtureMode='kimi-idle'", "returnByValue": True},
            )
            idle_mid = "msg_" + "2" * 32
            idle_kimi = cdp.deliver_background(
                kimi_binding, idle_mid, FixtureClient, priority=True
            )
            assert (
                idle_kimi["priority_action"]
                == "kimi_normal_send_after_priority_shortcut_noop"
            )
            actual = client.call(
                "Runtime.evaluate",
                {
                    "expression": "JSON.stringify({steered:window.fixtureSteered,promoted:window.fixturePromoted,submitted:window.fixtureSubmitted,old:!!document.querySelector('li[data-queue-item-id=old-queue]')})",
                    "returnByValue": True,
                },
            )
            observed = json.loads(actual["result"]["value"])
            assert (
                list(map(line_equivalent, observed["steered"])).count(
                    line_equivalent(
                        wake_text(false_busy_kimi_mid, "kimi", "session_fixture")
                    )
                )
                == 1
            )
            assert (
                observed["promoted"].count(
                    f"[HARNESS_BRIDGE_WAKE:{false_busy_zcode_mid}]"
                )
                == 1
            )
            assert (
                list(map(line_equivalent, observed["submitted"])).count(
                    line_equivalent(wake_text(idle_mid, "kimi", "session_fixture"))
                )
                == 1
            )
            assert observed["old"]
            # Wrong ID/draft and duplicate queue markers must not promote anything.
            marker = "[HARNESS_BRIDGE_WAKE:msg_" + "e" * 32 + "]"
            client.call(
                "Runtime.evaluate",
                {
                    "expression": f"queueWake({json.dumps(marker)},'duplicate-1');queueWake({json.dumps(marker)},'duplicate-2')",
                    "returnByValue": True,
                },
            )
            assert (
                client.evaluate("priority_queue_submit", zcode_binding, marker)["error"]
                == "priority_queue_ambiguous"
            )
            assert (
                client.evaluate(
                    "priority_queue_submit",
                    {**zcode_binding, "session_id": "wrong_session"},
                    marker,
                )["error"]
                == "session_identity_or_composer_mismatch"
            )
            client.call(
                "Runtime.evaluate",
                {
                    "expression": "document.querySelector('[contenteditable]').textContent='KEEP';document.querySelector('button.stop')?.remove();window.fixtureMode='normal'",
                    "returnByValue": True,
                },
            )
            assert (
                client.evaluate("priority_queue_submit", zcode_binding, marker)["error"]
                == "existing_draft"
            )
            client.call(
                "Runtime.evaluate",
                {
                    "expression": "document.querySelector('[contenteditable]').textContent=''",
                    "returnByValue": True,
                },
            )
            # A generating chat must block normal selection before a row is clicked.
            client.call(
                "Runtime.evaluate",
                {
                    "expression": "window.fixtureSelections=0;document.querySelector('.se').addEventListener('click',()=>window.fixtureSelections++);const stop=document.createElement('button');stop.className='stop';document.body.appendChild(stop)",
                    "returnByValue": True,
                },
            )
            busy = client.evaluate(
                "select",
                {
                    "harness": "kimi",
                    "session_id": "session_fixture",
                    "title": "Fixture",
                },
            )
            assert busy["error"] == "current_conversation_generating"
            selections = client.call(
                "Runtime.evaluate",
                {"expression": "window.fixtureSelections", "returnByValue": True},
            )
            assert selections["result"]["value"] == 0
            client.call(
                "Runtime.evaluate",
                {
                    "expression": "document.querySelector('button.stop').remove()",
                    "returnByValue": True,
                },
            )
            # Actual DOM draft guard; verify the existing text survives.
            client.call(
                "Runtime.evaluate",
                {
                    "expression": "document.querySelector('[contenteditable]').textContent='KEEP_DRAFT'",
                    "returnByValue": True,
                },
            )
            observed = client.evaluate(
                "focus",
                {
                    "harness": "kimi",
                    "session_id": "session_fixture",
                    "title": "Fixture",
                },
                "marker",
            )
            assert observed["error"] == "existing_draft"
            client.call(
                "Runtime.evaluate",
                {
                    "expression": "document.querySelector('[contenteditable]').textContent=''",
                    "returnByValue": True,
                },
            )
            client.call(
                "Runtime.evaluate",
                {
                    "expression": "let attachment=document.createElement('span');attachment.setAttribute('data-media-att-id','fixture');document.querySelector('.composer').appendChild(attachment)",
                    "returnByValue": True,
                },
            )
            observed = client.evaluate(
                "focus",
                {
                    "harness": "kimi",
                    "session_id": "session_fixture",
                    "title": "Fixture",
                },
                "marker",
            )
            assert observed["error"] == "existing_attachment_draft"
            client.call(
                "Runtime.evaluate",
                {
                    "expression": "document.querySelector('[data-media-att-id]').remove()",
                    "returnByValue": True,
                },
            )
            # Actual DOM wrong-session guard rejects even a correctly shaped marker.
            observed = client.evaluate(
                "submit",
                {"harness": "kimi", "session_id": "session_wrong", "title": "Fixture"},
                "marker",
            )
            assert observed["error"] == "session_identity_or_composer_mismatch"
        output = {
            "scope": "isolated headless CDP/DOM fixture; real desktop harnesses unverified",
            "results": results,
            "actual_dom_native_priority": True,
            "actual_dom_false_busy_native_priority": True,
            "actual_dom_idle_priority_shortcut_fallback_once": True,
            "actual_dom_priority_old_queue_preserved": True,
            "actual_dom_priority_ambiguity_guard": True,
            "actual_dom_draft_guard": True,
            "actual_dom_busy_selection_guard": True,
            "actual_dom_attachment_guard": True,
            "actual_dom_session_guard": True,
            "actual_dom_readonly_activity_and_unknown_guard": True,
            "actual_dom_exact_inactive_kimi_sidebar_and_ambiguity_guard": True,
            "actual_dom_zcode_controller_without_selected_or_rendered_target": True,
            "actual_dom_zcode_controller_identity_offline_phase_timeout_and_current_root_guards": True,
            "endpoint_owner_verified": True,
            "user_browser_profile_used": False,
        }
        (ROOT / "verification").mkdir(parents=True, exist_ok=True)
        (ROOT / "verification" / "cdp-fixture.json").write_text(
            json.dumps(output, indent=2) + "\n", encoding="utf-8"
        )
        print(json.dumps(output, ensure_ascii=False))
        client.close()
    finally:
        server.shutdown()
        server.server_close()
        try:
            children = psutil.Process(proc.pid).children(recursive=True)
            proc.terminate()
            for child in children:
                try:
                    child.terminate()
                except psutil.Error:
                    pass
            proc.wait(timeout=5)
        except (psutil.Error, subprocess.TimeoutExpired):
            if proc.poll() is None:
                proc.kill()


if __name__ == "__main__":
    main()
