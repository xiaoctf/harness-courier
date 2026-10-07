# 自动审批下使用 Harness Courier

## 能力与限制

发送和派发在 MCP 内部完成，模型无需额外运行派发 Shell 脚本。工具 annotations 区分读取和写入行为。该设计减少对完全访问模式的依赖，但不能授予宿主未允许的权限。

此仓库的自动化测试使用临时数据库和模拟目标，不证明各版本 Codex Desktop 的自动审批发送已通过。实际环境需重新加载 MCP，再在明确授权的测试会话验证发送、ACK 和结果。

## 发送流程

MCP 发送工作流：

1. `courier_send_message(dispatch=true)` 在服务器的 mailbox 中创建消息。
2. MCP 以服务器自身的 Python 解释器启动同目录的 `dispatch_background.py`，传服务器数据库、原消息 ID，以及固定的导航/插队布尔选项。
3. 固定 dispatcher 把原 ID 持久入队，有限后台 worker 逐条发送；MCP 返回异步受理状态。
4. `courier_wait_for_receipt` / `courier_get_message_status` 查询同一消息的 `dispatch_queue`、Hook 收件、ACK 和结果。

工具参数不允许调用方指定任意命令、脚本、数据库路径、网络地址或恢复选项。子进程不使用 Shell，不显示控制台窗口；MCP 的受理子进程限制为 45 秒，实际 worker 有独立有限生命周期。显式 `dispatch=false` 仍可只写 mailbox，后续 `courier_dispatch_message` 使用原 ID。已 ACK 或终态消息不再启动派发进程。队列、有限重试及安全暂停规则见 [派发与插队](dispatch-and-priority.md)。

新进程用于加载磁盘上的当前传输代码；MCP 自身的入口修改仍需重新加载服务。消息绑定、草稿、附件、忙碌状态、投递日志及回执保护沿用既有实现。超时或输出无法核验时保留原 ID，返回 `outcome_uncertain=true` 和 `submitted=null`，先检查原回执及日志，不重新创建任务，也不擅用恢复参数。

工具 annotations 按实际行为填写：`courier_list_sessions/status/wait` 标记只读；发送、取件、回执和绑定包含写入行为；绑定可能替换目标，发送和派发会访问目标桌面应用。只读查询、ACK 和相同终态回执标记幂等，其他工具保守标记为非幂等。annotations 描述行为，不授予执行权限。

## Codex 设置与验证边界

[官方 MCP 文档](https://learn.chatgpt.com/docs/extend/mcp?surface=cli)提供 `default_tools_approval_mode` 及 `tools.<tool>.approval_mode`。这是 MCP 工具审批设置；[自动审批](https://learn.chatgpt.com/docs/sandboxing/auto-review)则更换审批者，保留主 Agent 的沙箱约束，两者不能混为一个开关。

设计目标是让模型通过用户授权的 MCP 发送，不再额外执行派发 Shell 命令，减少使用完全访问模式的需求。MCP 仍会受到宿主审批和组织策略限制；授权不明、真实目标不匹配或危险操作仍可能被拒绝。不能保证每一条消息无提示通过，也不能通过此改动保证通用 Computer Use 的应用授权自动获批。

本项目的安装脚本不会调整 `sandbox_mode`、`approval_policy`、Provider 或认证。工具审批设置的具体支持情况应以所用客户端版本为准，不应据此放宽全局权限。

更新后需要重新加载 MCP 服务；不需要重启电脑。真实发送验证应选择明确许可的测试绑定，检查正确目标、原消息 ID、ACK 和完成结果。

## 给 Codex 的提示词

以下提示词用于 MCP 安装且重新加载之后，替换尖括号内容：

```text
在自动审批模式下使用 harness_courier，向已绑定的 <alias> 发送以下任务：<任务正文>。
先用 courier_list_sessions 确认目标绑定，未经授权不要替换绑定。
调用 courier_send_message(dispatch=true)，保存返回的 message_id；用 courier_wait_for_receipt / courier_get_message_status 查询同一个 ID 的 ACK 和结果。
若派发失败或超时，先查询原消息 ID，不重复创建任务；不要自行使用恢复或强制导航参数。
若宿主审批拒绝，说明具体拒绝原因并停下受影响的操作，不切换完全访问模式。
```

长期协作的完整职责、接收方回执和监督建议见 [Agent 协作指南](agent-workflow.md)；上面的短提示词只适合一次明确的发送任务。

## 验证范围

固定派发、准确 annotations、超时和异常响应、新 stdio 子进程链路有回归测试。所有写入测试使用临时数据库；派发链路测试使用不存在的应用路径，避免连接真实桌面。wheel 包含派发 helper 和 `composer_guard.js`。

真实桌面自动审批行为、各客户端重载、Kimi/ZCode 往返和通用 Cua 操作需要在目标环境另行验证。见 [开发说明](development.md)。

新选项 `allow_busy_navigation` / `priority` 不改变宿主审批，须按已有授权使用，见 [派发与插队](dispatch-and-priority.md)。
