# SharkRail

> 让 AI Agent 的进程执行拥有可验证的结局。

[![CI](https://github.com/SharkFury/SharkRail/actions/workflows/ci.yml/badge.svg)](https://github.com/SharkFury/SharkRail/actions/workflows/ci.yml)
[![Python 3.9+](https://img.shields.io/badge/python-3.9%2B-3776AB.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-0A7E8C.svg)](LICENSE)

[English](README.md) | [简体中文](README.zh-CN.md)

SharkRail 为 AI 编程 Agent、IDE 和自动化工具定义并实现一套开放、厂商中立的进程执行
可靠性契约。它的 Python 参考运行时在 Windows pipes/ConPTY、POSIX pipes/PTY、进程组、
Job Object 和 WSL 之上提供统一、版本化的会话模型。

SharkRail 是执行基础设施，不是终端模拟器、远程 Shell 或安全沙箱。

对于单平台、短时、非交互命令，应直接使用标准进程 API。只有当产品需要可验证完成、
有界输出、交互终端语义、进程树清理或跨系统的诚实契约时，SharkRail 才值得成为额外
依赖。

## 为什么需要 SharkRail？

启动子进程很简单，让自治 Agent 可靠地管理它却很难。Agent 必须能用机器可验证的
方式回答以下问题：

- 进程是否成功启动、退出，退出后的输出是否已经排空？
- 字节来自 stdout、stderr，还是合并的终端流？
- 命令是正常完成，还是被超时、取消或资源策略终止？
- 子孙进程是否已被清理？
- 输出是否被截断，丢弃了多少字节？
- 当前机器是否支持 PTY、resize、指定 Shell 或 WSL？

SharkRail 将这些答案转化为结构化结果、有序事件、稳定错误码和可发现的能力。

项目的公共目标不止是一份实现，而是把执行契约、参考运行时、一致性测试和真实失败
案例维护为共享基础设施。详见[公共价值设计](docs/VALUE.zh-CN.md)。

## 核心能力

- 不经过隐式 Shell 解析的 direct argv 执行
- 显式 cmd、Windows PowerShell、PowerShell 7、Bash 和 Zsh 执行
- Pipe 模式分离 stdout/stderr，终端模式使用原生 PTY/ConPTY
- 持久会话：输入、EOF、resize、interrupt、cancel、wait 和 dispose
- Windows Job Object 与 POSIX process group 进程树清理
- 有序生命周期事件、可恢复游标和无损 Base64 输出
- 对输出、输入、事件、会话、RPC 并发与执行时间实施有界策略
- CPU、内存、进程数、总时长与空闲时长限制
- MCP、stdio JSON-RPC 2.0 服务和异步 Python API
- health/stats、trace ID、脱敏审计日志和 OpenTelemetry 接口
- 运行时 capability negotiation 与主动 `doctor` 诊断

## 快速开始

SharkRail 当前面向早期使用者，建议从源码安装：

```bash
git clone https://github.com/SharkFury/SharkRail.git
cd SharkRail
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
python -m pip install -e .

sharkrail run --json -- python -c "print('hello from SharkRail')"
sharkrail capabilities --json
sharkrail doctor
```

Shell 解析始终需要显式声明：

```bash
sharkrail shell bash "printf 'hello\n'"
sharkrail shell pwsh "Write-Output hello"
```

Windows 下可以直接路由到 WSL：

```powershell
sharkrail run --target wsl --wsl-distribution Ubuntu -- python3 -c "print('hello')"
```

完整开发环境见 [BUILD.md](BUILD.md)。

## Agent 集成

为支持工具发现的 host 启动 MCP server：

```bash
sharkrail mcp
```

启动 newline-delimited JSON-RPC 服务：

```bash
sharkrail serve
```

每行输入和输出都是一条 JSON-RPC 2.0 消息。请求可以并发执行，调用方通过 `id`
匹配响应。

```json
{"jsonrpc":"2.0","id":1,"method":"runtime.hello","params":{}}
{"jsonrpc":"2.0","id":2,"method":"session.start","params":{"spec":{"executable":"python","argv":["-c","print('hello')"]}}}
```

完整方法、事件和错误契约见 [协议参考](docs/PROTOCOL.md)。

## 跨平台契约

| 能力 | Windows | Linux | macOS |
| --- | --- | --- | --- |
| Pipe 输出 | stdout/stderr 分离 | stdout/stderr 分离 | stdout/stderr 分离 |
| 交互终端 | pywinpty/ConPTY | 原生 PTY | 原生 PTY |
| Resize | 支持 | 支持 | 支持 |
| 进程树所有权 | Job Object | Process group | Process group |
| WSL target | 支持，后代清理为 best effort | 不适用 | 不适用 |

客户端必须调用 `runtime.capabilities`，不能只根据操作系统名称猜测行为。
PTY/ConPTY 本身是合并终端流，因此 SharkRail 不会伪造不存在的 stderr 分流。

## 可靠性边界

进程退出和会话完成是两个不同阶段。只有输出排空完成，或产生有界的 drain 错误后，
会话才进入终态。取消按 interrupt、terminate、kill tree 逐级升级；输出丢失与能力降级
都会显式报告。

在生产集成前，请阅读 [可靠性契约](docs/RELIABILITY.md) 和
[安全策略](SECURITY.md)。SharkRail 与调用者使用相同权限执行程序；运行不可信代码时，
仍需要容器、虚拟机、AppContainer 或 Windows Sandbox。

## 文档

从 [文档索引](docs/README.md) 开始，或直接查看：

- [产品范围与原则](docs/PRODUCT.md)
- [公共价值、维护承诺与证据](docs/VALUE.zh-CN.md)
- [系统架构](docs/ARCHITECTURE.md)
- [协议参考](docs/PROTOCOL.md)
- [配置与限制](docs/CONFIGURATION.md)
- [可靠性契约](docs/RELIABILITY.md)
- [可观测性](docs/OBSERVABILITY.md)
- [故障排查](docs/TROUBLESHOOTING.md)
- [版本策略](docs/VERSIONING.md)
- [路线图](ROADMAP.md)
- [构建与测试](BUILD.md)

## 项目状态

SharkRail 当前处于 alpha（`v0.1`）。本地执行核心已经实现，并在 Windows、Ubuntu、
macOS 的 Python 3.9、3.11 和 3.14 上持续测试。JSON-RPC 协议版本为 `1.0.0`；
Python 包到 1.0 之前仍可能通过发布说明和迁移指南引入不兼容调整。

已发布变化见 [CHANGELOG.md](CHANGELOG.md)，非承诺性的未来方向见
[ROADMAP.md](ROADMAP.md)。

## 社区与安全

- 贡献代码前请阅读 [CONTRIBUTING.md](CONTRIBUTING.md)。
- 使用帮助与诊断要求见 [SUPPORT.md](SUPPORT.md)。
- 安全漏洞请按照 [SECURITY.md](SECURITY.md) 私下报告。
- 社区参与遵循 [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md)。

## 许可证

SharkRail 采用 [MIT License](LICENSE)。
