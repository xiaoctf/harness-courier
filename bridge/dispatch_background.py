"""Dispatch an existing bridge message using a fresh background-only process.

Queue with courier_send_message(dispatch=False), then pass its actual message ID here.
Receipts remain available through courier_get_message_status / courier_wait_for_receipt.
"""

import argparse
import json
import sys

from cu_client import DriverError

from bridge import DEFAULT_DB, BridgeError, Mailbox


def recover_unreceived(
    box, message_id, expected_sha, *, task_body=False, resume_staged=False
):
    from cdp_delivery import deliver_background

    message = box.status(message_id)
    binding = box.binding(message["alias"])

    def guard():
        current_binding = box.binding(message["alias"])
        if current_binding != binding:
            raise DriverError("Recovery binding changed")
        return box.status(message_id)

    body = None
    if task_body:
        body = (
            f"这是原消息 {message_id} 的受控接收修复，不是新任务。"
            f"请使用本会话 Hook 的 caller_session_id，先 courier_receive_messages(message_id='{message_id}') "
            "读取这条精确消息，再 courier_acknowledge_message；不要 ACK 旧暂停或别的任务。"
            "若 Hook/MCP 暂不可用，保留具体错误，仍按以下已授权正文执行安全本地工作，"
            "不要只回复接管后结束。预算、窗口和停止门不刷新。\n\n" + message["body"]
        )
    delivery = deliver_background(
        binding,
        message_id,
        recovery_sha=expected_sha,
        recovery_status=guard,
        recovery_body=body,
        resume_staged=resume_staged,
    )
    current = box.status(message_id)
    return {
        k: current.get(k)
        for k in (
            "id",
            "alias",
            "session_id",
            "state",
            "acknowledged_at",
            "completed_at",
            "dispatch_error",
        )
    } | {"desktop_delivery": delivery}


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("message_id")
    parser.add_argument(
        "--db", default=DEFAULT_DB, help="The MCP server mailbox database"
    )
    parser.add_argument(
        "--allow-busy-navigation",
        action="store_true",
        help="Explicit user authorization to select the bound chat without stopping other generation",
    )
    parser.add_argument(
        "--recover-unreceived",
        action="store_true",
        help="Explicitly authorized one-time recovery; never automatic",
    )
    parser.add_argument("--expected-journal-sha256")
    parser.add_argument(
        "--task-body",
        action="store_true",
        help="Explicit authorized fallback: same queued message body, no new message; never automatic",
    )
    parser.add_argument(
        "--resume-staged",
        action="store_true",
        help="Complete exact unsent recovery draft only; requires task-body and reserved journal",
    )
    args = parser.parse_args()
    try:
        if args.recover_unreceived:
            if args.allow_busy_navigation:
                raise BridgeError(
                    "Busy navigation option is not supported with journal recovery"
                )
            if not args.expected_journal_sha256:
                raise BridgeError("Recovery requires --expected-journal-sha256")
            result = recover_unreceived(
                Mailbox(args.db),
                args.message_id,
                args.expected_journal_sha256,
                task_body=args.task_body,
                resume_staged=args.resume_staged,
            )
        else:
            if args.expected_journal_sha256 or args.task_body or args.resume_staged:
                raise BridgeError("Journal SHA is only accepted with explicit recovery")
            result = Mailbox(args.db).dispatch(
                args.message_id, allow_busy_navigation=args.allow_busy_navigation
            )
    except (BridgeError, DriverError, OSError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if result.get("dispatch_error") else 0


if __name__ == "__main__":
    raise SystemExit(main())
