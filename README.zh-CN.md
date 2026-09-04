# SharkRail

> Native execution rails for AI agents.

SharkRail 是面向 AI 编程 Agent、IDE 与自动化工具的本地跨平台命令/终端执行运行时。它将 Windows、Linux、macOS 和 WSL 不同的进程机制适配为版本化契约，提供结构化输出、生命周期事件、超时取消、进程树清理和 capability 查询。

它是执行基础设施，不是终端 UI、远程 Shell，也不是安全沙箱。

## 它解决什么问题

Agent 不能像人一样观察提示符、手动按 Ctrl+C 或关闭窗口。它需要机器可验证地知道：进程是否启动、stdout/stderr 来自哪里、退出后输出是否排空、取消是否清理了子孙进程，以及输出是否被截断。

SharkRail v0.1 已实现：

- 不经过隐式 Shell 的 direct argv 执行；
- 显式 cmd、Windows PowerShell、PowerShell 7、Bash、Zsh 脚本执行；
- Pipe 模式独立 stdout/stderr；
- Linux/macOS 原生 PTY 与 Windows ConPTY；
- 可持久会话及 stdin、EOF、resize、interrupt、cancel、wait、dispose；
- interrupt → terminate → kill_tree 取消升级；
- Windows Job Object 与 POSIX process group 进程树管理；
- 有序事件、增量游标、字节级输出上限与截断统计；
- 有界事件分页、会话过期、累计输入和 RPC 背压；
- CPU、内存、进程数、总时长、空闲时长与 drain 超时策略；
- UTC/单调时钟事件、trace ID、health/stats、session 检查与主动 doctor 探测；
- 结构化 stderr 日志、脱敏 JSONL 审计和可选 OpenTelemetry；
- stdio JSON-RPC 2.0 服务；
- Native/WSL target 路由；
- capability negotiation 与 `doctor` 诊断；
- Windows、Ubuntu、macOS 的持续集成测试。

## 快速开始

```bash
python -m venv .venv
source .venv/bin/activate       # Windows: .venv\Scripts\activate
python -m pip install -e .

sharkrail run --json -- python -c "print('hello')"
sharkrail capabilities --json
sharkrail doctor
```

显式执行 Shell：

```bash
sharkrail shell bash "printf 'hello\n'"
sharkrail shell pwsh "Write-Output hello"
```

启动供 Agent host 调用的协议服务：

```bash
sharkrail serve
```

每行输入/输出是一条 JSON-RPC 2.0 消息。完整方法、事件与错误契约见 [协议文档](docs/PROTOCOL.md)。

## 平台语义

| 能力 | Windows | Linux | macOS |
| --- | --- | --- | --- |
| Pipe 输出 | stdout/stderr 分离 | stdout/stderr 分离 | stdout/stderr 分离 |
| 交互终端 | ConPTY | 原生 PTY | 原生 PTY |
| 进程树 | Job Object | Process group | Process group |
| WSL target | 支持，Linux 后代清理为 best effort | 不适用 | 不适用 |

客户端应调用 capability 接口，不能只根据操作系统名称猜测能力。PTY/ConPTY 是合并终端流，SharkRail 不会伪造 stderr 分流。

## 文档

- [英文 README](README.md)
- [产品与架构](docs/PRODUCT.md)
- [协议参考](docs/PROTOCOL.md)
- [可靠性契约](docs/RELIABILITY.md)
- [可观测性](docs/OBSERVABILITY.md)
- [构建与测试](BUILD.md)
- [贡献指南](CONTRIBUTING.md)
- [安全策略](SECURITY.md)

## 安全边界

SharkRail 与调用者使用相同权限执行程序，不提供恶意代码隔离。运行不可信代码时，请额外使用 Windows Sandbox、AppContainer、容器或虚拟机。

## 许可证

MIT，详见 [LICENSE](LICENSE)。
