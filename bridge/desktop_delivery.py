"""Deliver only an opaque wake marker; the receiver Hook checks the actual ID.

The user's message body never passes through an unverified desktop chat.
"""

from __future__ import annotations

import contextlib
import re
from pathlib import Path

from cu_client import DriverError

ROOT = Path(__file__).resolve().parent
APPS = {"kimi": "Kimi Code", "zcode": "ZCode"}


@contextlib.contextmanager
def desktop_lock(timeout=20):
    """Wait briefly across processes; retain the legacy shared lock boundary."""
    import os
    import time

    if os.name != "nt":
        raise DriverError("Desktop delivery requires native Windows")
    import msvcrt

    path = ROOT / "data" / "desktop.lock"
    path.parent.mkdir(exist_ok=True)
    # Windows permits locking a byte past EOF. Do not append on each contender.
    with path.open("a+b") as handle:
        deadline = time.monotonic() + timeout
        while True:
            handle.seek(0)
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                break
            except OSError as exc:
                if time.monotonic() >= deadline:
                    raise DriverError(
                        "Desktop delivery wait timed out; retry the original message ID"
                    ) from exc
                time.sleep(min(0.1, max(0, deadline - time.monotonic())))
        try:
            yield
        finally:
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)


def tree_lines(state):
    return (state.get("accessibility") or {}).get("tree", "").splitlines()


def line_index(line):
    match = re.match(r"\[(\d+)\]", line)
    return int(match.group(1)) if match else None


def field(line, name):
    match = re.search(r"\b" + name + r'="([^"]*)"', line)
    return match.group(1) if match else ""


def exact_kimi_session(state):
    ids = set()
    for line in tree_lines(state):
        if "文档" not in line and "Document" not in line:
            continue
        match = re.search(r'app://renderer/sessions/([A-Za-z0-9_-]+)(?:["/?#]|$)', line)
        if match:
            ids.add(match.group(1))
    return next(iter(ids)) if len(ids) == 1 else None


def zcode_header_title(state):
    lines = tree_lines(state)
    start = next(
        (i for i, line in enumerate(lines) if "@container/workspace-header" in line),
        None,
    )
    if start is None:
        return None
    titles = [
        field(line, "name")
        for line in lines[start + 1 : start + 12]
        if re.match(r"\[\d+\] (?:标题|Heading) ", line)
    ]
    return titles[0] if len(titles) == 1 else None


def current_matches(state, binding):
    if binding["harness"] == "kimi":
        return exact_kimi_session(state) == binding["session_id"]
    return zcode_header_title(state) == binding["title"]


def rect(line):
    match = re.search(r"\brect=(-?\d+),(-?\d+) (\d+)x(\d+)", line)
    if not match:
        return None
    x, y, w, h = map(int, match.groups())
    return x, y, x + w, y + h


def sidebar_title_targets(state, title):
    targets = []
    row = None
    for line in tree_lines(state):
        if "chat-header" in line or "@container/workspace-header" in line:
            break
        cls = field(line, "class")
        if re.match(r"\[\d+\] (?:组|Group) ", line) and cls.split()[:1] == ["se"]:
            row = rect(line)
        elif "group/task-item" in cls:
            row = rect(line)
        elif cls.split()[:1] == ["gh"] or "sortable" in line:
            row = None
        if re.match(r"\[\d+\] (?:文本|Text) ", line) and field(line, "name") == title:
            text_rect = rect(line)
            if row is None or text_rect is None:
                continue
            left = max(row[0], text_rect[0], 0)
            top = max(row[1], text_rect[1], 0)
            right = min(row[2], text_rect[2])
            bottom = min(row[3], text_rect[3])
            space = state.get("coordinate_space", {})
            right = min(right, space.get("image_width", right))
            bottom = min(bottom, space.get("image_height", bottom))
            if right - left < 4 or bottom - top < 4:
                continue
            # UIA text can extend beyond a clipped sidebar row. Click its visible
            # leading text, never the center of the unbounded text element.
            targets.append(
                {
                    "x": int(left + min(20, (right - left) / 2)),
                    "y": int((top + bottom) / 2),
                }
            )
    return targets


def compose_index(state, harness):
    candidates = []
    for line in tree_lines(state):
        if harness == "kimi":
            good = field(line, "name") == "消息输入框" and "ProseMirror" in line
        else:
            good = bool(re.match(r"\[\d+\] (?:编辑|Edit) ", line)) and field(
                line, "name"
            ) in (
                "提出后续修改要求",
                "输入消息",
                "输入消息...",
                "输入消息…",
                "发送消息",
                "向 ZCode 发送消息",
            )
        if good and "disabled=true" not in line:
            candidates.append(line_index(line))
    if len(candidates) != 1:
        raise DriverError("Cannot identify one editable message composer")
    return candidates[0]


def input_is_empty(state, index):
    lines = tree_lines(state)
    start = next((i for i, line in enumerate(lines) if line_index(line) == index), None)
    if start is None:
        return False
    if field(lines[start], "value").strip():
        return False
    # ProseMirror exposes text in its children, rather than ValuePattern.
    for line in lines[start + 1 :]:
        if re.match(
            r"\[\d+\] (?:按钮|Button|状态|Status|切换按钮|ToggleButton) ", line
        ):
            break
        if re.match(r"\[\d+\] (?:文本|Text) ", line) and field(line, "name").strip():
            return False
    return True


def deliver_wake(binding, message_id, client_factory=None, *, dispatch_guard=None):
    # New processes use only the background Electron transport. No foreground
    # fallback, even when the endpoint is absent or the composer is unavailable.
    from cdp_delivery import CdpClient, deliver_background

    return deliver_background(
        binding, message_id, client_factory or CdpClient, dispatch_guard=dispatch_guard
    )
