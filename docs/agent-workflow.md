# Agent 协作与提示词

这份指南将实际使用中形成的任务派发、监督和回执建议整理为通用用法。它是可选的项目规则示例，需服从使用者的当前授权和宿主权限；安装 MCP 不会自动安装这些提示词，也不会赋予额外执行权。

## 放在哪里

- [Codex 发送方规则](prompts/codex-controller.md)：复制正文到发送项目的 `AGENTS.md`，或该客户端支持的项目指令中。
- [Kimi/ZCode 接收方规则](prompts/kimi-zcode-receiver.md)：复制正文到接收项目支持的项目指令文件或会话指令中。
- [任务卡模板](prompts/task-card.md)：填入真实信息后，作为 `courier_send_message` 的 `body`。消息正文最多 6000 个字符；长合同使用接收者已获准读取的文件，并在任务卡中保留关键边界与队列。

这里只提供文本模板，不修改任何客户端的全局提示词。三个客户端的项目指令机制以各自版本为准。

## 一次完整往返

1. 在实际 Kimi/ZCode 会话触发已安装的接收 Hook。用 `courier_list_sessions` 查真实会话及绑定，人工确认对应项目与目标。
2. 首次绑定使用 `courier_bind_target`，别名应区分项目和角色，例如 `projectA-kimi-review`、`projectA-zcode-dev`。已有正确绑定不用重建；替换目标需明确授权。
3. Codex 填好任务卡，调用 `courier_send_message(dispatch=true)`，保存返回的消息 `id`。后续工具参数使用 `message_id` 传同一个值。
4. 接收者用 Hook 提供的 `caller_session_id` 与当前消息 ID 收件并 ACK，执行许可范围内的完整职责队列。
5. Codex 用 `courier_wait_for_receipt` / `courier_get_message_status` 查看同一消息。接收方完整完成自己的职责后调用 `courier_return_result(result_kind="completed")`；无法继续时用 `failed` 说明原因及剩余工作。
6. Codex 对照实际代码、测试和产物独立验收。接收者终态回执和父任务全部完成是两件事。

绑定固定目标会话和版本，但不会替使用者验证完整项目目录与写权。Hook 的 `cwd`、聊天标题和在线状态只是线索；任务卡应明确工作目录，接收方执行前核对。`sender_session_id` 不会自动取得真实 Codex 聊天身份；缺少可验证身份时不要编造。回执保存在消息记录中，当前没有自动返回或唤醒原 Codex 聊天的调度器。

## 工具参数示例

以下 JSON 用于说明参数，不是需要逐条执行的脚本。`<target_session_id>` 必须来自实际会话，`<caller_session_id>` 必须来自接收 Hook，`<message_id>` 必须替换为发送返回的 `id`。首次绑定只执行一次；接收工具在对应 Kimi/ZCode 客户端调用。

```json
[
  {"tool": "courier_list_sessions", "arguments": {}},
  {"tool": "courier_bind_target", "arguments": {"alias": "projectA-kimi-review", "harness": "kimi", "session_id": "<target_session_id>"}},
  {"tool": "courier_send_message", "arguments": {"alias": "projectA-kimi-review", "body": "<完整任务卡>", "dispatch": true}},
  {"tool": "courier_receive_messages", "arguments": {"caller_session_id": "<caller_session_id>", "message_id": "<message_id>"}},
  {"tool": "courier_acknowledge_message", "arguments": {"caller_session_id": "<caller_session_id>", "message_id": "<message_id>"}},
  {"tool": "courier_wait_for_receipt", "arguments": {"message_id": "<message_id>", "timeout": 30, "until": "acknowledged"}},
  {"tool": "courier_get_message_status", "arguments": {"message_id": "<message_id>"}},
  {"tool": "courier_return_result", "arguments": {"caller_session_id": "<caller_session_id>", "message_id": "<message_id>", "body": "<产物、验证证据和限制>", "result_kind": "completed"}}
]
```

## 消息状态与工作状态

| 证据 | 可以确认什么 | 不能据此确认什么 |
| --- | --- | --- |
| `queued` | 消息已入库 | 接收者已经看到或开始执行 |
| `dispatch_queue.pending/working` | worker 等待或尝试派发 | 对方已收件、ACK 或优先消费 |
| `dispatch_queue.held/exhausted` | 该条派发暂停或重试耗尽，其他条目可继续 | 可以绕过日志保护或创建副本重发 |
| `desktop_delivery.submitted=true` | 桌面传输报告提交 | Hook 收件、ACK、任务执行或成功 |
| `delivered` | 接收 Hook 或取件工具已将消息标记送达 | Agent 已经读懂并执行任务 |
| `acknowledged` | 对应会话调用了 ACK | 实际动作、阶段产物或最终正确性 |
| `completed` / `failed` | 对应会话发送了终态回执 | 协调者已经独立验收或父目标已经闭合 |

实际开工应有命令、文件改动或其他可核验事件；阶段完成应有对应产物；验收需要与风险相称的独立检查。`online`、mtime、计划文字和单次无进程观察都不足以证明进度或停工。

`courier_get_agent_status` 增加精确会话的只读运行观察：当前原生停止按钮与编辑器共同提供 `running/idle`，近期 Stop、工具结束和审批 Hook 提供独立事件与时间。查询不会切换聊天或唤醒任务。未选中的聊天没有已验证原生状态时保留 `unknown`，近期 Hook 仍可显示；事件过期不能证明当前状态。出现 `idle_with_unfinished_messages` 或 `progress_needs_verification` 时检查实际输入消费、产物和合法后继。不要自动重发 ACK 任务或停止进程。字段和加载限制见 [运行状态说明](agent-status.md)。

## 等待、重试与接续

`courier_wait_for_receipt` 的 `timeout` 是 0..55 秒，`until` 只能是 `acknowledged` 或 `completed`；它默认等待终态，达到时限则返回当前状态，终态 `failed` 也会结束等待。可先等 ACK，再等结果。更长的任务应按预计时长采用宿主真实支持的低频等待或已授权调度，不紧密轮询，也不反复播报无变化。

成功入队后保存原 ID。`dispatch_queue.pending/working` 表示 worker 接管，不再手动争抢派发。未 ACK 时不要盲目再次 `courier_send_message`；失败或超时先检查原记录。确认普通派发尚未成功、目标和草稿安全且允许重试时，可以调用 `courier_dispatch_message(message_id=原ID)`。提交结果不确定或投递日志保护拒绝时停止自动重试，检查回执和日志；不要自行使用恢复、强制导航或前台输入绕过保护。ACK 后监督已有任务，不重复派发。

常规缺陷留在原任务的职责队列。输入、owner、权限或必要前提实质变化，或旧职责已终态但父目标仍有剩余时，才发送必要的版本化修订/接续卡，注明父目标、旧 ID、已接受成果与完整剩余队列。这是新指令，不是对旧 ID 的重试；旧任务活跃时先明确交接和写权，避免两个 owner 同时修改。

## 失败时查哪一层

| 层 | 先核对的证据 |
| --- | --- |
| 配送与 MCP | 原 ID、实际绑定、工具参数、`dispatch_error`、Hook 收件及 ACK |
| 任务卡 | 第一实质动作、真实目录、输入版本、权限、队列、依赖与终态是否明确 |
| 实现与 runtime | 实际入口、异常、进程生命周期、依赖、产物及最近动作 |
| 等待与授权 | 是否在合法等待、是否已耗尽职责、是否需要新的用户授权 |

有证据的异常应主动调查。界面检查只使用宿主允许且可用的工具，核对相关绑定会话，保留草稿和健康作业；不能因为检查失败就自行清空输入、Stop、重启客户端或修改权限。没有 UI 能力时继续只读检查原回执与受控日志，并明确能力缺口。不要把每个实现 bug 都写成提示词问题，也不要用 ACK 冒充恢复成功。

## 后台控制与边界

普通消息派发使用 CDP，接收方通过 Hook 取正文。可选 Cua 是另一套能力；在实际工具支持时显式使用 `delivery_mode="background"`，遇到 `background_unavailable` 或策略拒绝不自动改成前台。不要为发消息再操作物理鼠标，或把完全访问作为默认故障修复。

提示词不能保证 Agent 永久运行、自动跨 turn 唤醒、独占工作区或形成可靠调度。只有受支持的实际机制才能提供这些能力。消息内容是任务数据；它不能改写宿主权限，也不能授权发布、凭据外传、付费、删除或其他范围外操作。

并发、授权切换和原生插队的参数与限制见 [派发与插队](dispatch-and-priority.md)。
