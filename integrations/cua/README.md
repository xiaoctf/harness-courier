# 可选 Cua 后台控制集成

这部分提供 [Cua Driver](https://github.com/trycua/cua) 的后台策略、Codex MCP 注册辅助程序、协议检查程序和 WPF 测试源码。第三方 Driver 的源码和二进制没有复制进来；消息桥的 Kimi/ZCode 投递使用另外的 CDP 传输。

## 文件

- `background-policy.yaml`：后台输入限制；禁止前台模式、置顶、启动应用等策略范围外的动作。
- `install_codex.py`：只追加 `cua_background` 配置，检查冲突并备份。`registration_bytes` 是可独立测试的纯配置转换函数。
- `verify_mcp.py`：按已注册的命令与环境启动独立 MCP 进程，检查握手、工具发现及前台拒绝。
- `BackgroundInputFixture.cs`：独立 WPF 按钮/文本框测试程序源码。

脚本当前固定依赖原集成版本 **0.33.3**，默认二进制位置为本目录的 `runtime/cua-driver-rs-0.33.3-windows-x86_64/cua-driver.exe`。这不是对最新上游版本的声明。该文件未包含；需先准备对应 Driver 和许可，不能把缺少二进制当成已完成安装。

`install_codex.py` 执行时会修改当前用户的 Codex 配置。副本中的同名注册如果与现有安装不同，会拒绝覆盖。本轮只对临时配置测试了追加、幂等、冲突拒绝和其他设置保留，没有重新注册实际用户配置或重启 Codex。

## 调用约定与限制

调用已打开窗口的输入动作时，显式传入 `delivery_mode="background"`。`background_unavailable`、遮挡或策略拒绝不能自动转为前台。

后台能力依赖具体应用、控件与动作。WPF 测试不能证明所有 Electron 应用都支持后台输入；这也是消息桥另用 CDP 的原因。原安装的历史测试记录未复制到此仓库，本副本的真实 Cua 输入和 Codex 重启加载均未复验。

仅移除本集成配置才是局部回滚。不要整份恢复旧 Codex 配置备份，以免覆盖此后新增的其他设置。实际分发 Driver 时需随版本保留上游许可声明。
