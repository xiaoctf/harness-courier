# 发布准备

当前版本为 `0.1.0` 预览版，项目自身代码使用 MIT。公开源码仓库为 [xiaoctf/harness-courier](https://github.com/xiaoctf/harness-courier)，预览版标签为 `v0.1.0-preview`。发布文件与说明见 [GitHub Releases](https://github.com/xiaoctf/harness-courier/releases)。尚未发布到 PyPI。

当前 `main` 包含持久发送队列、有限重试、忙碌聊天切换、默认原生插队及可选强制策略、漏 Hook 收件兼容和只读运行状态查询。原 `v0.1.0-preview` 标签与 Release 下载包保留，未覆盖为新源码；需要升级时使用当前 `main`。本机 Kimi Code 1.0.4 / ZCode 3.14.4 已验证真实回执、优先消费和状态查询；多个独立 Codex 并发与跨版本、跨机器桌面验收仍待完成。

源码包包括桥接服务、接收 Hook、后台启动器、可选 Cua 集成源码和策略、测试、配置示例、Agent 协作指南及发送方/接收方/任务卡模板。Cua Driver/KimiCU 二进制需要用户另行准备；消息派发使用独立 CDP 传输。

## 本地导出

先审阅并将需要发布的源码纳入本地 Git 索引，再运行：

```powershell
python scripts/prepare_release.py --output C:\path\to\new-release-directory
```

输出目录和同名 `.zip` 必须尚不存在。脚本只读取当前 Git 跟踪的允许文件，检查常见凭据和机器专用路径，再生成源码目录、ZIP 和逐文件 SHA-256 清单。Git 历史、`source-snapshot.json`、数据库、配置、日志、备份、运行目录、验证回执、构建产物和第三方二进制不会导出。原始开发历史保留在本地，公开时应从已审阅的导出树创建干净仓库，不直接推送本地开发历史。

扫描是针对已知模式的辅助检查，不能证明不存在所有隐私或凭据。发布前还需审阅实际包内文件。版权署名使用项目贡献者的集合名称；如果需要个人或机构署名，应在正式发布前明确修改。

## 验证和限制

本地检查需包括 Ruff、当前 185 项回归、wheel/sdist 构建，以及公开副本再次运行测试。wheel 必须包含 `composer_guard.js`、`agent_observer.js`、`agent_activity.py`、`wake_protocol.py`、`priority_policy.py`、`dispatch_runner.py`、`dispatch_queue.py`、`dispatch_worker.py`、`priority_delivery.py`、新旧 Python 包和许可证。GitHub Actions 工作流配置了 Windows / Python 3.11 与 3.13 的检查；每次提交的实际结果见 [Actions](https://github.com/xiaoctf/harness-courier/actions)。本地结果不替代该提交的远程 CI 结果。

本版本尚无跨机器真实桌面兼容性验收、自动审批发送验收、完整多项目调度或自动返回原 Codex 聊天。源码和配置准备完成不等于这些能力完成。

若计划申请专利，应先评估申请与公开的时间顺序；本地导出不等于向公众披露。首次公开由仓库所有者授权；后续发布者仍应遵守各自项目的发布权限。
