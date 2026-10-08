# 并发派发、会话切换与插队

本地修复增加持久发送队列及以下选项，须重新加载 MCP 后使用。生产旧名称是 `bridge_send` / `bridge_dispatch`；开源版名称为 `courier_send_message` / `courier_dispatch_message`，参数相同。

- 派发进程现在最多等待共享发送锁 20 秒，避免刚遇到并发就失败。同一时刻仍只有一个桥接进程操作聊天界面；AI 的任务运行不占这把发送锁。
- `allow_busy_navigation` 默认 `true`，允许从正在生成的聊天切到精确绑定的另一聊天。它不停止原聊天、不清空草稿，也不允许猜测未显示的目标。
- `priority` 默认 `true`，请求客户端原生插队，可能打断目标会话当前生成。使用者须允许目标任务被打断。未配置强制策略时，显式 `priority=false` 可使用普通排队。

## 本机强制插队策略

在实际 mailbox 数据库所在目录放置 `delivery-policy.json`，内容为 `{"force_priority":true}`。这是管理员的本机配置，不是 MCP 调用参数。开启后，入队、worker 取件和直接派发都会将 `priority=false` 覆盖为 `true`，调用者不能降级。策略读取失败时停止派发，不悄悄改为普通发送；缺少配置或设置为 `false` 时保留调用参数。

策略也适用于已明确请求派发但尚未提交的 pending 项；已经提交、ACK、完成或提交不确定的记录不重放。`dispatch=false` 仍只保存消息。异步入队返回 `requested_priority` 与 `effective_priority`，实际原生动作仍看原 ID 的 `dispatch_queue.last_delivery` 和接收方回执。

## 持久发送队列

`dispatch=true` 先把原消息 ID 写入 SQLite 的 `dispatch_jobs`，返回后由固定后台 worker 派发，不必让每个 Codex 聊天等待桌面锁。每个 mailbox 同时只有一个 worker 实际投递；不同 mailbox 仍使用共享桌面锁。优先消息先派发，同优先级按入队时间排序；暂时失败并处于退避期的消息不堵住后面的可发送消息。不承诺不同 mailbox 之间公平。

队列只处理明确请求派发的 ID，不扫描、重投历史消息。`dispatch=false` 只写 mailbox，不启动发送 worker。已注册且接收保护有效的离线聊天可以进入发送队列；实际投递仍须找到绑定的原会话，不能改投其他目标。

状态查询增加 `dispatch_queue`，与接收状态分别判断：

| 队列状态 | 含义 |
| --- | --- |
| `pending` | 等待 worker 或下一次有限重试 |
| `working` | worker 正在尝试派发 |
| `submitted` | 桌面提交得到确认，仍需等真实 Hook/ACK/result |
| `held` | 草稿、身份拒绝、提交不确定或 worker 中断，暂停该条消息 |
| `exhausted` | 自动重试次数或时间窗口已耗尽 |
| `cancelled` | 接收方已 ACK 或终态，取消多余派发 |

`attempts` 是当前重试窗口的尝试次数；`last_error` 和 `last_delivery` 提供最近原因。`worker_start_requested`/PID 仅说明请求启动子进程，不证明 worker 获锁或 AI 收件。发送返回 `submitted=null` 表示异步受理，不能据此报告已送达。

仅对确认尚未开始输入的临时连接、锁等待或界面就绪故障自动重试，最多 6 次、10 分钟，退避为 2/5/15/30/60 秒。草稿、附件、身份冲突和不确定提交不会自动重试。输入前先写入并同步投递日志，避免丢失响应后重复输入。

修复原因后，可对原 ID 调用 dispatch：仅最近失败明确安全且不存在投递日志的 `held`/`exhausted` 才开启新的有限窗口。已经提交或存在不确定日志时保留保护，不创建副本。worker 中断后的 `working` 会暂停；未开始的 `pending` 由下一次派发或 Codex MCP 启动继续处理，过期项停止。worker 空闲后退出，不是永久驻留服务。

## 发送与原生插队

示例：

```json
{"alias":"projectA-kimi-review","body":"先处理这条更正，保留已有产物。","dispatch":true,"allow_busy_navigation":true,"priority":true}
```

保存返回的 `id`，后续以 `message_id` 查询相同消息的 ACK/result。`priority_requested` 和 `priority_action` 仅说明桥接调用的原生入口，不证明 AI 已消费该消息。

Kimi 在发送时使用当前版本的 Ctrl+Enter 对当前输入执行 steer，不以此前的忙碌探测结果决定是否尝试；没有使用 Ctrl+S，因为该版本有旧队列时 Ctrl+S 可能 steer 旧队首。快捷键后若仍是同一会话、唯一输入框且精确草稿未变，才允许重新保护检查后普通发送；快捷键响应丢失或草稿改变时停止。ZCode 先提交本次精确 wake，再检查具有相同 marker 的唯一队列项并执行原生“立即发送”，即使此前忙碌探测返回 false。保留其他队列项，不笼统点击 Stop，不自动清队列。

等待锁和会话切换后会重新读取消息状态和绑定；已经 ACK 或完成则取消发送。提交/插队结果不确定时保留日志防重放，不能盲目创建副本或反复点击。既有成功提交也不能用重试变成再次插队。

## 验证边界

跨进程锁、并发 mailbox 写入、固定子进程参数、ACK 防重放和隔离浏览器中精确队列选择已验证。本机 Kimi Code 1.0.4、ZCode 3.14.4 的独立测试聊天已验证运行中原生插队、真实收件、ACK/result 及已有草稿保护；没有向业务聊天发送测试消息。这些版本的本地结果不代表其他版本或机器兼容。

持久队列、优先级排序、同优先级 FIFO、有限重试和双 Windows worker 互斥已通过隔离回归；多个独立 Codex 发送方的真实同时派发仍需桌面验收。原 Codex 聊天自动唤醒尚未实现。草稿、目标不可见、应用接口持续不可用或提交不确定时仍会暂停或耗尽重试。原生插队也不保证后台子任务或独立进程已经停止，旧任务回执不会被自动伪造为 completed。

其他环境的真实验收需要明确许可的测试聊天，在运行中注入新 ID 并核到真实 ACK、优先消费及旧队列保留；不要使用业务聊天代替隔离验收。

参考：[Kimi 官方输入说明](https://www.kimi.com/code/docs/kimi-code-desktop/input-and-context.html)，以及本机安装版本的 composer handler 和 ZCode 队列组件。

`dispatch=false` 仍仅入队，不导航、不插队；不要同时传 true 的插队/导航选项。常规派发 CLI 默认同样启用插队与切换；未开启强制策略时，`--no-priority` 可关闭插队，`--no-allow-busy-navigation` 可关闭切换。原有受控恢复不继承这些默认值。
