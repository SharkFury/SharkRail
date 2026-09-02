# SharkRail

> Native execution rails for AI agents.
>
> 面向 AI 编程 Agent 的跨平台命令执行运行时，Windows-first。

SharkRail 是一个供 AI Agent、IDE 和自动化工具调用的本地执行基础设施。项目的前身是通过 Pipe 重定向 `cmd.exe` 的 Win32 实验原型，现在正按全新的跨平台产品方向重启。

它不负责生成代码，也不打算再做一个终端界面。它要解决的是更靠近操作系统、却被多数 Agent 重复处理的问题：**可靠地启动命令、流式读取输出、驱动交互程序、判断执行完成、取消整棵进程树，并在 Windows、Linux 和 macOS 上提供一致的上层协议。**

> [!IMPORTANT]
> 项目目前处于 vNext 产品设计与重启阶段。下面描述的是目标产品，而不是已经发布的功能。原有代码是早期 Win32 Pipe/Winsock 原型，不代表 vNext 的完成度。

## 为什么需要它

AI Agent 调用终端时，需要的不只是一次 `spawn()`：

- 编译器、测试工具与 Git 需要分离的 `stdout`、`stderr` 和可信退出码；
- REPL、调试器和 TUI 需要真实的终端语义、输入和窗口尺寸；
- 超时或取消必须清理子进程、孙进程以及仍持有管道的后台进程；
- 大量输出需要背压、上限、截断标记和可恢复的读取游标；
- Windows 的 Pipe、Console、ConPTY、Job Object、控制事件和 WSL 各有不同语义；
- 跨平台 Agent 不应在每个产品里重新实现这些边界条件。

现有 PTY 库解决了“如何打开伪终端”，终端模拟器解决了“如何显示终端”，而 SharkRail 关注的是 **Agent 可依赖的执行生命周期和协议契约**。

## 产品定位

**Windows-first，协议跨平台。**

上层 Agent 使用同一套版本化协议；运行时根据目标系统选择原生后端：

| 能力 | Windows | Linux | macOS |
| --- | --- | --- | --- |
| 非交互执行 | `CreateProcessW` + Pipe | `posix_spawn`/`exec` + Pipe | `posix_spawn` + Pipe |
| 交互终端 | ConPTY | PTY | PTY |
| 进程树管理 | Job Object | Process Group；可选 cgroup | Process Group |
| 中断/终止 | Console control event / ConPTY 输入 + Job | `SIGINT`/`SIGTERM`/`SIGKILL` | `SIGINT`/`SIGTERM`/`SIGKILL` |
| Linux 子系统 | WSL target adapter | 不适用 | 不适用 |

平台差异不会被隐藏。客户端可通过 capability negotiation 得知当前后端支持什么、保证到什么程度。

## 计划提供的核心能力

### 1. 三种执行方式

- `exec(mode=pipe)`：默认模式，适合构建、测试、Git 和脚本；保留独立的 `stdout`、`stderr`。
- `exec(mode=pty)`：适合必须检测 TTY 的一次性程序；输出是带 VT 序列的合并字节流。
- `terminal.open()`：持久交互会话，适合 Shell、REPL、调试器和 TUI；支持输入、调整尺寸和增量读取。

### 2. 可预测的生命周期

统一事件顺序：

```text
accepted -> started -> output(seq) -> exited -> drained -> completed
```

`completed` 表示进程退出且可读输出已经排空，而不是通过匹配提示符猜测命令是否完成。

### 3. 可靠取消与资源回收

取消采用分级策略：

```text
interrupt -> grace period -> terminate -> kill process tree
```

Windows 会以 Job Object 作为会话进程树的生命周期边界；POSIX 系统使用 process group，并通过 capability 说明更强隔离能力是否可用。

### 4. 面向 Agent 的输出模型

- 单调递增的事件序号和读取游标；
- 输出大小与内存上限；
- 背压和明确的 `output.truncated` 事件；
- 原始字节与解码文本分层处理；
- 稳定的错误码、失败阶段与完成原因。

### 5. 多种接入方式

运行时核心与 Agent 协议解耦，计划提供：

- CLI；
- 本地 stdio JSON-RPC；
- MCP adapter；
- ACP adapter；
- 后续的语言 SDK 与本地 Named Pipe transport。

## 目标接口示例

以下接口用于说明设计方向，尚未作为稳定版本发布。

```powershell
# 非交互执行，参数不会先拼接成 shell 字符串
sharkrail exec --target windows --mode pipe -- git status --short

# 需要终端语义的一次性程序
sharkrail exec --target windows --mode pty -- python -i

# 打开持久 WSL 终端
sharkrail terminal open --target wsl --shell bash
```

协议请求示意：

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "session.start",
  "params": {
    "target": "windows",
    "mode": "pipe",
    "command": {
      "kind": "direct",
      "executable": "git.exe",
      "argv": ["status", "--short"]
    },
    "cwd": "C:\\work\\demo",
    "timeoutMs": 30000
  }
}
```

直接执行与 Shell 执行必须显式区分，避免 Windows 命令行引用规则、PowerShell 解析规则和 POSIX Shell 规则彼此混淆。

## 架构概览

```text
Agent / IDE / CLI
        |
  ACP / MCP / CLI adapters
        |
  Versioned local protocol
        |
  Session Manager
  - state machine
  - events and output store
  - quotas and cancellation
        |
  Target Router
   /       |        \
Windows   Linux     macOS
Pipe/     Pipe/     Pipe/
ConPTY    PTY       PTY
Job       PGID      PGID
```

详细的产品范围、语义契约、架构决策、验收标准和路线图见 [产品文档](docs/PRODUCT.md)。

## 设计原则

1. **正确性优先于“看起来能跑”**：退出、排空和取消都有明确状态。
2. **Pipe 默认，PTY 按需**：自动化命令不为交互兼容性牺牲结构化输出。
3. **统一意图，不伪造平台等价性**：使用稳定动作和结果，差异通过 capability 暴露。
4. **协议与实现解耦**：MCP、ACP 和 SDK 是 adapter，不侵入执行核心。
5. **本地优先、最小权限**：MVP 不监听公网、不提权、不把 Job Object 宣称为安全沙箱。
6. **可测试的边界条件**：把编码、管道继承、进程树、背压和竞态纳入一致性测试。

## 范围边界

v0.1 不计划提供：

- 远程 TCP Shell 或多用户服务；
- 权限提升与凭据管理；
- 安全沙箱或容器隔离；
- GUI 自动化；
- 完整终端 UI；
- 通过 Prompt/正则表达式推断命令完成；
- 运行时重启后的会话持久化。

## 路线图

- **M0 — Reboot**：产品、协议、安全与贡献文档；归档并标注旧原型。
- **M1 — Foundation**：核心状态机、Win32 RAII 封装、测试 fixtures 和 Windows CI。
- **M2 — v0.1 alpha**：Windows Pipe/ConPTY/Job Object、CLI、stdio JSON-RPC、基础 WSL 支持。
- **M3 — Cross-platform preview**：Linux/macOS Pipe + PTY backend，共享一致性测试。
- **v0.2**：Named Pipe、Windows ARM64、可选终端屏幕模型、WSL 内部 supervisor。
- **v0.3**：MCP/ACP adapter、SDK/C API、更多宿主集成。
- **v1.0**：稳定协议、兼容策略和经过验证的平台支持矩阵。

## 参与项目

当前最需要的是对产品契约和失败场景的反馈，包括：

- 真实 Agent 在 Windows 上执行失败或卡住的复现案例；
- `cmd.exe`、Windows PowerShell、PowerShell 7、WSL 和常见开发工具的兼容样本；
- 进程树取消、输出背压、Unicode、超长参数和句柄继承测试；
- MCP/ACP adapter 的最小接口设计。

在首个实现 PR 之前，请先以 issue 形式描述用户场景、预期语义和可复现命令。

## 系统要求

- Windows 后端目标基线：Windows 10 1809 / Windows Server 2019 或更高版本；
- v0.1 首先支持 x64，ARM64 进入后续里程碑；
- Linux/macOS 后端将在跨平台预览阶段给出明确的最低版本。

## 许可证

原项目采用 [GNU General Public License v3.0](https://github.com/larrychen1024/Win32ConsoleProxy/blob/master/LICENSE)。SharkRail 在发布可执行产物前应完成依赖许可证审计，并明确是否继续沿用 GPL-3.0。合并本设计稿时请保留原仓库中的完整 `LICENSE` 文件。

## 参考

- [Microsoft: Pseudoconsoles](https://learn.microsoft.com/windows/console/pseudoconsoles)
- [Microsoft: Job Objects](https://learn.microsoft.com/windows/win32/procthread/job-objects)
- [Agent Client Protocol](https://agentclientprotocol.com/)
- [Model Context Protocol](https://modelcontextprotocol.io/)
