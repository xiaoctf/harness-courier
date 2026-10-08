# Harness Courier · 会话信使

[English](README.md) | **简体中文**

Windows 桌面端 Codex、Kimi Code、ZCode 之间的本地消息桥。Codex 把消息发给**绑定的真实会话**，接收方通过 Hook 取件，再用 MCP 返回 ACK 和处理结果。

这是 Windows 桌面集成的预览版，使用本地 MCP、SQLite mailbox 和接收 Hook。运行数据库、真实聊天记录、日志、用户配置和第三方二进制均不属于源码分发。

项目代码采用 [MIT License](LICENSE)。Codex、Kimi Code、ZCode 和 Cua 是各自独立的产品；本项目为社区集成，不代表这些产品的官方支持。

## 能力与边界

- 按真实会话 ID 和绑定版本投递；别名变化不会让旧消息改投另一聊天。
- SQLite 持久化消息、绑定和回执。`queued → delivered → acknowledged → completed/failed` 分别表示入队、接收 Hook 取件、Agent 确认、Agent 返回结果。
- Kimi/ZCode 的桌面唤醒使用经过进程归属核验的本地 CDP 端口，只输入接收会话限定的唤醒元数据；正文由 Hook 或按精确消息 ID 的收件工具取出，兼容 Kimi 插队跳过接收 Hook 的情况。
- 发送前检查会话、输入框、草稿、附件和忙碌状态。结果不确定时保留投递日志并阻止自动重复发送。
- 提供可选 Cua Driver 后台控制策略及注册辅助脚本。它与消息桥的 CDP 传输分别实现；后台能力取决于应用和控件。

回执保存在对应消息记录中，由发送方查询。当前没有完整实现“自动唤醒原 Codex 聊天”的调度器；可使用 Codex 宿主中已配置的心跳查询原消息。多个项目应使用不同别名，例如 `projectA-kimi-review`，并分别绑定目标会话；持久发送队列通过每个 mailbox 的串行 worker 和共享桌面锁协调派发，多个独立 Codex 会话同时操作真实桌面的验收仍待完成。

## 环境与配置

需要 Windows、Python 3.11+、`psutil` 和 `websocket-client`。开发工具为 Ruff 和 Python build；依赖声明见 [pyproject.toml](pyproject.toml)。

建议在源码目录使用独立环境：

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

复制 [apps.example.toml](apps.example.toml) 为仓库根目录的 `apps.toml`，填写自己的应用安装路径。也可通过 `HARNESS_COURIER_APPS` 指定配置文件的绝对路径，旧 `HARNESS_BRIDGE_APPS` 作为回退。启动器和 CDP 发送器共用这份配置；错误字段、相同端口、相对应用路径和缺失的显式配置会被拒绝。

没有配置文件时使用与示例一致的 `Program Files` 占位路径；程序不会自动发现应用安装目录。首次使用应填写自己的 `apps.toml`。默认值集中在 `bridge/harness_courier/app_registry.py`，`apps.toml` 被 Git 排除。

## 后台启动

两份 CMD 都调用**此仓库内**的同一启动实现：

```powershell
.\launcher\start_background.cmd --check
.\launcher\start_background.cmd --app all
```

解释器依次使用 `HARNESS_COURIER_PYTHON`、旧 `HARNESS_BRIDGE_PYTHON`、仓库 `.venv`、Windows `py -3`、PATH 中的 `python`。可将 `HARNESS_COURIER_PYTHON` 设为 Python 可执行文件的完整路径。

`--check` 只检查，不启动或关闭应用；返回 0 表示检查完成，应同时查看 `background_ready`。应用已运行但后台端口未就绪时，普通启动会要求先保存工作并退出应用；程序不会自行强制关闭它。新进程以最小化且不激活窗口的方式启动，保留原用户配置和聊天。

`--autostart` 保留登录启动模式：延迟后执行、记录 `bridge/data/startup.log`、不等待 Enter。此参数不会自行创建 Windows 开机启动项。

## MCP 与接收 Hook

安装后推荐使用 `harness-courier mcp --harness codex`；新安装的 MCP 注册名为 `harness_courier`。`harness-courier-start` 是后台启动 CLI。

`bridge/bridge.py` 是兼容入口，旧参数和默认数据库位置保持不变：

```powershell
.\.venv\Scripts\python.exe bridge\bridge.py mcp --harness codex
.\.venv\Scripts\python.exe bridge\bridge.py --db C:\path\to\mailbox.sqlite3 peers
```

三个客户端需要连接同一个 mailbox，并安装 Kimi/ZCode 的接收 Hook。`bridge/install_integrations.py` 默认生成安装计划，`--apply` 才写入配置。已存在的同名配置若指向其他安装，会拒绝覆盖；已有安装应先核对注册归属，避免覆盖当前工作的桥。

```powershell
.\.venv\Scripts\python.exe bridge\install_integrations.py
# 审阅预览后，显式安装 MCP 和接收 Hook：
.\.venv\Scripts\python.exe bridge\install_integrations.py --apply
```

应用配置和 MCP 安装路径必须保持有效；移动目录后需要重新核对注册。设置完成后重新加载三个客户端，让实际会话运行接收 Hook。用 `courier_list_sessions` 查到目标的真实会话，再用 `courier_bind_target` 分别绑定项目别名；不要凭聊天标题猜测会话 ID。

发送端工具：`courier_list_sessions`、`courier_bind_target`、`courier_send_message`、`courier_dispatch_message`、`courier_get_message_status`、`courier_wait_for_receipt`。接收端工具：`courier_receive_messages`、`courier_acknowledge_message`、`courier_return_result`，以及公共查询工具。接收方必须使用 Hook 提供的真实 `caller_session_id`。工具发现也保留对应 `bridge_*` 别名，旧提示词可继续使用。

监督外部 AI 可用发送端新增的 `courier_get_agent_status(alias=目标别名)`（旧名 `bridge_agent_status`）。它只读查询精确绑定会话，区分正在生成、当前空闲、近期结束事件、等待审批、等待用户输入和无法确认；消息查询也附带原接收会话的 `agent` 状态。支持的 ZCode 版本按工作目录和精确会话 ID 查询后台 controller，不依赖当前选中聊天或侧栏渲染；旧 Stop 超期不会使有效的当前观察降为 unknown。空闲但仍有未终结回执会提示检查，不能把空闲或 ACK 当成任务完成。新工具需重新加载 Codex MCP；新增 Hook 在新建或安全恢复的外部会话中加载，不强制重启业务聊天。详见 [运行状态说明](docs/agent-status.md)。

默认发送流程在 MCP 内执行固定后台派发，无需再让模型额外运行派发 Shell 脚本。[自动审批说明](docs/codex-approvals.md)记录配置边界、验证结果和使用提示词。

## 协作提示词

[Agent 协作指南](docs/agent-workflow.md)说明如何绑定目标、派发完整任务、读取 ACK、监督执行和独立验收。可复制的模板分别为 [Codex 发送方](docs/prompts/codex-controller.md)、[Kimi/ZCode 接收方](docs/prompts/kimi-zcode-receiver.md)和[任务卡](docs/prompts/task-card.md)。按项目调整后放入支持的项目指令文件；这些文本不会随 MCP 自动加载或授予权限。

命名、状态含义及迁移细节见 [命名与兼容性](docs/naming-and-compatibility.md)。新旧入口共用实现；本次改名不迁移数据库、绑定或唤醒协议。

## 开发结构

| 位置 | 职责 |
| --- | --- |
| `bridge/harness_courier/mailbox.py` | SQLite 事务、绑定、消息状态、回执 |
| `bridge/harness_courier/mcp_tools.py` | 按 harness 暴露工具并路由调用 |
| `bridge/harness_courier/mcp_server.py` | stdio JSON-RPC 解析、通知、错误响应 |
| `bridge/harness_courier/cli.py` | 参数和 CLI 入口 |
| `bridge/harness_courier/app_registry.py` | 应用路径、端口和进程归属校验 |
| `bridge/harness_courier/launcher.py` | 后台启动、只读检查、登录日志 |
| `bridge/harness_courier/composer_guard.js` | 当前会话和输入框的 DOM 保护 |
| `bridge/dispatch_runner.py` | MCP 内的固定子进程派发与准确工具标注 |
| `bridge/dispatch_queue.py` / `dispatch_worker.py` | 持久发送队列、有限重试与单 worker 派发 |
| `bridge/cdp_transport.py` | 选定 renderer、提交、确认和投递日志 |
| `bridge/install_integrations.py` | MCP 与接收 Hook 的安装预览、幂等写入 |
| `bridge/receiver_hook.py` | 接收事件、真实身份和消息上下文 |
| `integrations/cua/` | 可选 Cua 后台控制集成，不含 Driver 二进制 |

[开发与验证说明](docs/development.md)记录架构约定、测试方式和本轮验证范围。本地发布准备和分发检查见 [发布说明](docs/release.md)。

## 发布状态

版本 `0.1.0` 按预览版准备。自动化测试覆盖 mailbox、MCP、Hook、配置与传输保护，但不能替代各桌面版本的真实往返测试。当前没有跨机器兼容性或多项目并发调度保证；自动审批下的实际发送、桌面重载及可选 Cua 输入需在各自环境验证。

Cua Driver 来自 [trycua/cua](https://github.com/trycua/cua)，是外部依赖；分发相应版本时需保留其许可与第三方声明。本仓库没有打包它的二进制。

依赖和第三方许可边界见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)，贡献检查见 [CONTRIBUTING.md](CONTRIBUTING.md)。

当前源码增加持久队列、有限重试、有时限的锁等待、忙碌聊天切换、原生插队及可选强制策略、漏 Hook 收件兼容和运行状态查询，包含 185 项回归。发送异步受理后查询 `dispatch_queue`、原 ID 的真实回执及接收会话状态。本机 Kimi Code 1.0.4 / ZCode 3.14.4 已验证真实往返、运行中优先消费和状态观察，包含 ZCode 未选中聊天且旧 Stop 超期时的当前空闲状态；只使用一个 Codex 发起环境，多个独立发送方并发与跨版本、跨机器验收仍待完成。旧 `v0.1.0-preview` Release 下载包不包含此次升级，请使用当前 `main` 源码；每次提交的实际 CI 结果见 [Actions](https://github.com/xiaoctf/harness-courier/actions)。见 [派发与插队](docs/dispatch-and-priority.md)。

派发默认原生插队并允许切换忙碌聊天；仅管理员未开启 `force_priority` 时，显式 `priority=false` 才使用普通排队。开启后入队和投递均覆盖 false，草稿与身份保护仍生效。
