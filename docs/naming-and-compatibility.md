# 命名与兼容性

项目名为 **Harness Courier**，中文名为**会话信使**。它将任务发到绑定的桌面会话，并保存接收者的 ACK 和结果。当前按 `0.1.0` 预览版准备。

## 公共入口

| 用途 | 推荐名称 | 兼容名称 |
| --- | --- | --- |
| GitHub 仓库 / Python 分发包 | `harness-courier` | 不同时安装旧 `harness-bridge` 分发包 |
| Python 包 | `harness_courier` | `harness_bridge` |
| MCP 注册名 | `harness_courier` | 已有 `harness_bridge` 注册保留 |
| CLI | `harness-courier` | `harness-bridge` |
| 后台启动 CLI | `harness-courier-start` | `harness-bridge-start` |
| 应用配置环境变量 | `HARNESS_COURIER_APPS` | `HARNESS_BRIDGE_APPS` |
| CMD 解释器环境变量 | `HARNESS_COURIER_PYTHON` | `HARNESS_BRIDGE_PYTHON` |
| 可选第三方后台控制 MCP | `cua_background` | 注册名不变 |

两个环境变量前缀同时存在时，以 `HARNESS_COURIER_` 为准。显式指定的应用配置无效会报错，不回退到旧配置。Python 分发名变化不自动卸载旧包；从旧分发包升级应在独立虚拟环境安装新版，避免两个分发包争用同名兼容入口。

仓库名是建议名称；本地改名不预留 GitHub/PyPI 名称，也不构成商标结论。

## MCP 工具

| 推荐工具 | 旧工具别名 | 行为 |
| --- | --- | --- |
| `courier_list_sessions` | `bridge_peers` | 查询真实会话与绑定 |
| `courier_bind_target` | `bridge_bind` | 绑定别名到指定会话 |
| `courier_send_message` | `bridge_send` | 创建新消息，可同时尝试唤醒 |
| `courier_dispatch_message` | `bridge_dispatch` | 派发已有消息 ID |
| `courier_get_message_status` | `bridge_status` | 查询消息状态与结果 |
| `courier_wait_for_receipt` | `bridge_wait` | 有界等待 ACK 或终态 |
| `courier_receive_messages` | `bridge_inbox` | 为当前真实会话取件 |
| `courier_acknowledge_message` | `bridge_ack` | 确认已读 |
| `courier_return_result` | `bridge_reply` | 返回成功或失败的终态结果 |

此预览版的工具发现同时暴露新名称和旧别名，新名称排在前面。每个 harness 仍只能访问自己的角色工具；别名不会增加权限。新旧名称共用参数、annotations、数据库和同一消息 ID，可以混用。发送新消息、派发已有消息、返回终态结果分别是三种操作。

## 状态与身份

| 数据库状态 / 传输字段 | 中文含义 |
| --- | --- |
| `queued` | 已入队 |
| `submitted` | 已提交桌面唤醒；这是传输字段，不是数据库消息状态 |
| `delivered` | 接收 Hook 已取件 |
| `acknowledged` | 接收 Agent 已读确认 |
| `completed` | 已返回成功结果；仍需发送方独立验收 |
| `failed` | 已返回失败结果 |

别名推荐 `<project>-<harness>-<role>`，例如 `projectA-kimi-review`。这是命名约定，不是项目访问隔离机制。消息身份仍由真实会话 ID、绑定版本与消息 ID 决定。

## 源码组织

实现集中在 `bridge/harness_courier/`；旧 `harness_bridge` 子模块是同一模块对象的别名，保留类身份和模块级 mock/patch 语义。`bridge.py` 和旧脚本入口继续工作。

| 当前实现 | 旧入口 |
| --- | --- |
| `harness_courier/mcp_tools.py` | `harness_bridge/tools.py` |
| `harness_courier/mcp_server.py` | `harness_bridge/mcp.py` |
| `harness_courier/app_registry.py` | `harness_bridge/apps.py` |
| `harness_courier/composer_guard.js` | `harness_bridge/composer.js` 兼容资源 |
| `cdp_transport.py` | `cdp_delivery.py` |
| `dispatch_runner.py` | `mcp_dispatch.py` |
| `install_integrations.py` | `setup_bridge.py` |
| `integrations/cua/` | `background-control/` 中的两个脚本转发入口 |

默认数据库继续是入口目录下的 `data/bridge.sqlite3`，唤醒协议继续使用 `HARNESS_BRIDGE_WAKE`，Hook 配置的原块标记保留。改名不迁移现有数据库、回执、绑定或启动项。

安装器对新安装使用 `harness_courier`；已存在且指向本安装的旧注册会原样保留，不添加第二份服务。新旧任一同名条目指向其他安装，或两种注册同时存在时，安装器拒绝写入，需先核对归属。Cua Driver 仍是独立第三方依赖，后台控制能力和消息传输分开验证。
