# 消息桥模块

使用和开发入口见仓库根目录 [README](../README.md) 与 [开发说明](../docs/development.md)。

`bridge.py` 保留脚本调用和导入兼容性；实现已经拆到 `harness_courier/`。本目录的数据目录在运行时生成，并被 Git 排除。测试只写入临时数据库，不应使用原安装的业务 mailbox。

## 不变量

1. 消息入队时固定目标会话和绑定版本，不能在重试时改投其他目标。
2. 桌面输入成功仍为 `queued`；接收 Hook 取件后才是 `delivered`。
3. ACK 和 result 校验接收方会话；终态重放不会重新执行，冲突终态回执拒绝更新。
4. 普通发送只提交唤醒标记；恢复操作要求原消息 ID、精确投递日志 SHA 和即时未接收状态。恢复不是普通重发接口。
5. 当前 CDP 传输不激活窗口、不调用 OS 鼠标键盘或剪贴板。会话、草稿、忙碌或提交结果不确定时停止，不转为前台输入。

`desktop_delivery.py` 保留全局锁和旧 accessibility 解析函数；`cu_client.py` 保留旧驱动诊断客户端的兼容接口。普通消息派发入口 `deliver_wake` 只走 CDP，不会自动调用这个旧客户端。

## 入口

- `bridge.py`：MCP 服务与 CLI。
- `receiver_hook.py`：Kimi/ZCode 接收 Hook。
- `setup_bridge.py`：默认预览；`--apply` 写入明确归属本桥的配置。
- `start_background.py` / `.cmd`：调用共享启动实现。
- `dispatch_background.py`：对已入队消息按原 ID 派发；恢复参数需查阅实现中的保护条件，不能用于盲目重投。
- `verify_cdp_fixture.py`：独立 headless Chrome 测试页，记录实际 DOM 验证结果，不读取用户浏览器配置。

当前副本的验证范围见开发说明。原安装的历史真实会话回执没有复制过来，不能用于证明本副本已经完成桌面上线。
