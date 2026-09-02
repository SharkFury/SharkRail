# SharkRail 产品文档

| 项目 | 内容 |
| --- | --- |
| 产品名称 | SharkRail |
| CLI 名称 | `sharkrail` |
| 文档状态 | Draft / 产品与架构提案 |
| 产品阶段 | Reboot，尚无可用的 vNext 版本 |
| 产品定位 | Windows-first 的跨平台 AI Agent 执行运行时 |
| 首要用户 | AI 编程 Agent、IDE/Agent 开发者、自动化工具作者 |
| 首个目标平台 | Windows 10 1809+ / Windows Server 2019+，x64 |

## 1. 决策摘要

SharkRail 不再被定义为“远程控制 `cmd.exe` 的代理”，而被定义为：

> 为 AI Agent 提供可预测、可取消、可观测的本地命令与终端会话，并通过统一协议适配 Windows、Linux、macOS 和 WSL。

产品采取 **跨平台协议 + 平台原生后端 + Windows-first 实现** 的策略。

第一阶段的竞争力不来自支持最多的 Agent 协议，也不来自做终端 UI，而来自把 Windows 上最容易出错的执行细节做成可信基础设施：

- 区分 Pipe 与 PTY/ConPTY；
- 精确定义进程退出、输出排空和会话完成；
- 管理整棵进程树；
- 支持分级取消、超时和强制回收；
- 处理流式输出、背压、截断、编码与句柄继承；
- 用 capability 表达跨平台差异，而不是假装系统语义完全一致。

### 1.1 命名决策

产品正式名称确定为 **SharkRail**，对外短句为：

> Native execution rails for AI agents.

名称由两部分构成：

- **Shark**：提供鲜明、易记、适合形成吉祥物和社区文化的品牌识别；
- **Rail**：代表一条有边界、可观测、可取消的可靠执行轨道。不同操作系统拥有不同原生轨道，但向上提供统一契约。

Shark 是品牌人格，Rail 是产品承诺。技术定位始终通过副标题说明，避免用户把它误解为通用 Agent 框架或终端 UI。

建议使用以下命名体系：

| 用途 | 名称 |
| --- | --- |
| 产品与开源项目 | SharkRail |
| CLI | `sharkrail` |
| 协议 | SharkRail Protocol |
| 核心库 | `sharkrail-core` |
| 后台进程（如需要） | `sharkraild` |
| 配置目录 | `.sharkrail/` |

备选名称：

| 名称 | 优点 | 不作为首选的原因 |
| --- | --- | --- |
| ExecRail | 技术定位直接，适合基础设施项目 | 记忆度和视觉延展弱于 SharkRail |
| ExecTrellis | 有基础设施感，初步检索未发现明显同名包 | 名称较长，Trellis 与执行的关系需要解释 |
| CommandRail | 产品含义直观，初步检索未发现明显同名包 | 名称较长，品牌力度稍弱 |
| SpawnWeave | 技术感强，能表达多后端进程创建 | `spawn` 更像底层 API，产品价值表达偏窄 |
| ProcRelay | 简短，突出进程控制和协议中继 | 对非系统开发者不够直观 |
| ShellSpan | 易读，能表达跨 Shell/跨系统 | 容易让人误解为只支持 Shell 或 PTY |

已排除的名称：

- `ExecWeave`：已经被一个 AI Agent 可观测性项目使用，并已发布同名 PyPI 包；
- `CommandWeave`：存在多个近似同名的 `CommandWeaver` GitHub 项目，不利于搜索和长期品牌识别；
- `Runstead`、`Runwright`、`Runplane`、`Runifold`：均已有活跃产品或开源项目。

截至本次初步检索，`SharkRail` 没有 GitHub 精确同名仓库，npm、PyPI 与 crates.io 也没有同名包。搜索结果中存在铁路设备和汽车零件对 “Shark rail” 的描述性使用，但未发现直接的软件项目冲突。

以上是开源生态的初步重名筛查，不构成商标法律意见。对外发布前仍应核验 GitHub 组织/仓库名、目标语言包名、域名、社交账号和主要市场商标。

## 2. 背景与问题

### 2.1 Agent 的执行需求不同于人类终端

人类可以看到 Prompt、判断命令是否结束、手动按 `Ctrl+C`，并在进程卡住时关闭窗口。Agent 需要机器可验证的答案：

- 进程是否成功启动？
- 哪些内容来自 `stdout`，哪些来自 `stderr`？
- 主进程退出后，所有输出是否已经读取完？
- 超时发生在启动、运行、排空还是关闭阶段？
- 取消后是否仍有编译器、测试进程或服务留在后台？
- 输出被截断时丢失了多少数据？
- 当前会话是否真的支持 TTY、resize 或软中断？

这些不是 UI 问题，而是执行运行时的契约问题。

### 2.2 Windows 为什么格外困难

Windows 同时存在多套接口和行为：

- Win32 进程创建与命令行字符串序列化；
- 标准句柄继承与 Pipe EOF；
- 传统 Console API 和现代 ConPTY；
- Console control event、终止进程与 Job Object；
- `cmd.exe`、Windows PowerShell、PowerShell 7 各自的解析规则；
- WSL 的 Windows 进程边界与 Linux 进程树边界。

Linux/macOS 的 Pipe、PTY、信号和 process group 组合相对一致，但仍存在 shell、session、daemonize 和输出排空问题。因此，产品可以跨平台，**但不能只用一套最小公分母实现所有平台**。

### 2.3 原项目现状

原仓库是一个早期 C++ 实验：使用匿名管道重定向 `cmd.exe` 的输入输出，并包含简单的 Winsock Client/Server 示例。它验证了基本方向，但缺少面向 Agent 产品所需的生命周期、并发、协议、安全和测试设计。

vNext 应把旧代码视为历史原型：可借鉴 Win32 调研，但不直接把现有网络服务扩展成生产接口。

## 3. 产品愿景

让任意 Agent 在本地调用同一套执行协议，并获得如下保证：

1. 命令按调用者提供的结构化参数启动；
2. 输出可以流式、增量、有界地读取；
3. 交互程序获得真实终端语义；
4. 完成、失败、超时和取消具有稳定定义；
5. 运行时尽最大可能回收整棵进程树；
6. 平台能力与限制可被程序发现；
7. Agent 不需要理解 Win32、ConPTY、PTY 或 signal 的实现细节。

## 4. 目标用户与核心任务

### 4.1 Agent 框架开发者

希望用一个本地组件替代自建的 `subprocess`、PTY、超时、输出缓存和清理逻辑，并通过 MCP、ACP 或 SDK 接入。

### 4.2 IDE 与开发工具开发者

希望同时支持 Windows、WSL、Linux 和 macOS，并获得统一事件流，而不在每个平台维护一套隐含行为。

### 4.3 高级 Agent 用户与 CI 工具作者

希望复现和诊断“命令卡住、取消不干净、输出丢失、乱码、只在 Windows 失败”等问题。

### 4.4 典型使用场景

- 执行构建、测试、lint、Git 和包管理命令；
- 运行需要 TTY 检测的 CLI；
- 驱动 PowerShell、Python/Node REPL、调试器和 TUI；
- 长时间运行任务的增量观察与取消；
- Windows 与 WSL 目标之间的显式路由；
- 为不同 Agent 产品提供相同的底层执行能力。

## 5. 产品范围

### 5.1 v0.1 必须具备

- Windows `pipe` 模式；
- Windows `pty` 模式（ConPTY）；
- 持久终端会话；
- stdin 写入与关闭；
- PTY resize；
- 退出码、错误阶段和完成原因；
- Job Object 进程树管理；
- interrupt、terminate、kill 和 timeout；
- 有界输出、背压、读取游标与截断事件；
- CLI 与 stdio JSON-RPC；
- Windows direct command、`cmd`、Windows PowerShell、PowerShell 7；
- 基础 WSL target，明确标注 best-effort 清理限制；
- 诊断命令与 Windows CI 一致性测试。

### 5.2 跨平台预览范围

- Linux Pipe + PTY backend；
- macOS Pipe + PTY backend；
- 使用 process group 管理子进程；
- 同一协议、状态机、事件格式和一致性测试；
- 输出平台 capability 和已知限制。

### 5.3 明确不做

- 公网监听或远程 Shell；
- 多租户执行服务；
- 权限提升、UAC 自动处理或凭据保管；
- 安全沙箱、容器或恶意代码隔离；
- GUI 自动化；
- 终端窗口和完整渲染 UI；
- 依靠 Prompt、颜色或正则匹配判断生命周期；
- 任意附加到已经运行的 Console；
- v0.1 会话跨运行时重启持久化；
- 修改用户的 Shell Profile；
- 在没有 WSL 内部 supervisor 时承诺完全清理 Linux 后台进程。

## 6. 跨平台适配策略

### 6.1 统一的是意图和结果

上层协议统一以下动作：

- `start`
- `write`
- `close_stdin`
- `resize`
- `interrupt`
- `cancel`
- `wait`
- `subscribe`
- `dispose`

统一以下结果：

- 是否被接受与成功启动；
- 进程标识与会话标识；
- 有序输出事件；
- 退出码或信号等平台信息；
- 完成原因；
- 错误码与失败阶段；
- 是否发生输出截断或资源限制。

### 6.2 不统一伪等价的底层机制

| 上层意图 | Windows 实现 | Linux/macOS 实现 |
| --- | --- | --- |
| `interrupt` | Console control event，或向 ConPTY 写入对应控制输入 | 向 process group 发送 `SIGINT` |
| `terminate` | 终止主进程或受控 Job，取决于策略 | 向 process group 发送 `SIGTERM` |
| `kill_tree` | 终止/关闭 Job Object | 向 process group 发送 `SIGKILL` |
| `resize` | `ResizePseudoConsole` | `TIOCSWINSZ` |
| 终端输出 | ConPTY UTF-8 + VT 合并流 | PTY 合并流 |
| Pipe 输出 | 独立原始 `stdout`/`stderr` | 独立原始 `stdout`/`stderr` |

`interrupt` 是一个跨平台意图，不保证每个目标程序都会处理它。运行时必须在结果中说明采取了什么动作，并在宽限期后按策略升级。

### 6.3 Capability negotiation

客户端在执行前可读取能力：

```json
{
  "protocolVersion": "0.1",
  "platform": "windows",
  "architecture": "x86_64",
  "targets": ["windows", "wsl"],
  "modes": ["pipe", "pty", "terminal"],
  "features": {
    "separateStderrInPipe": true,
    "separateStderrInPty": false,
    "resize": true,
    "softInterrupt": "best_effort",
    "processTreeKill": "job_object",
    "wslProcessTreeKill": "best_effort"
  }
}
```

客户端不得根据操作系统名称猜能力；版本新增能力时，老客户端应能忽略未知字段。

### 6.4 Backend 接口

核心状态机只依赖内部 backend contract：

```text
ExecutionBackend
  capabilities() -> Capabilities
  start(spec, io, lifecycle) -> ProcessHandle
  write(handle, bytes)
  close_stdin(handle)
  resize(handle, cols, rows)
  signal(handle, intent)
  wait(handle) -> NativeExit
  kill_tree(handle)
  dispose(handle)
```

Windows、Linux、macOS 和 WSL adapter 分别实现该接口。协议层不直接调用 Win32 或 POSIX API。

## 7. 执行模型

### 7.1 Pipe execution

面向非交互命令，是默认选择。

特性：

- 直接程序与参数数组；
- 独立 `stdout`/`stderr`；
- stdin 可选；
- 原始字节优先，文本解码为附加层；
- 可信退出码；
- 不分配终端。

适合构建、测试、Git、编译器、脚本和数据处理。

### 7.2 PTY execution

面向需要 TTY 的一次性命令。

特性：

- Windows 使用 ConPTY，POSIX 使用 PTY；
- 支持窗口尺寸和 VT 序列；
- `stdout`/`stderr` 通常合并，协议不得伪造分流；
- 会话仍以子进程生命周期为权威完成条件。

### 7.3 Persistent terminal

面向 Shell、REPL、调试器和 TUI。

特性：

- 长期存在的 PTY/ConPTY；
- 原始输入、命名按键和 resize；
- 输出游标和 scrollback policy；
- 可选 VT parser 与 screen snapshot；
- 不保证自动识别 Shell 内每一条命令的边界。

Shell integration、OSC 标记和 Prompt 识别只能提供可选元数据，不能替代运行时的生命周期状态机。

### 7.4 Direct command 与 Shell command

请求必须显式选择：

```text
DirectCommand {
  executable,
  argv[]
}

ShellCommand {
  shell: cmd | powershell | pwsh | bash | zsh,
  script
}
```

Direct command 避免不必要的 Shell 注入和二次解析。Windows backend 负责正确构造 `CreateProcessW` 所需命令行；Shell command 则按指定 Shell 的规则执行。

## 8. 生命周期契约

### 8.1 状态机

```text
created
  -> accepted
  -> starting
  -> running
  -> exiting
  -> draining
  -> completed

任何阶段 -> failed
running/exiting -> cancelling -> draining -> completed
```

### 8.2 事件顺序

同一会话内保证：

```text
session.accepted
process.started
stdout | stderr | pty.output  (0..n, seq 单调递增)
process.exited
session.drained
session.completed
```

`process.exited` 不代表最后一段输出已经到达；只有 `session.completed` 才表示调用者可以得到最终结果。

### 8.3 完成原因

```text
exited
cancelled
timed_out
killed
start_failed
runtime_failed
resource_limited
```

完成原因与退出码分开。被强制终止的进程可能存在平台退出码，但不能被误报为正常退出。

### 8.4 取消升级

默认策略：

1. 发送软中断；
2. 等待 `interruptGraceMs`；
3. 执行 terminate；
4. 等待 `terminateGraceMs`；
5. kill 整棵受控进程树；
6. 排空剩余输出并完成会话。

调用者可跳过软中断直接强制取消，但策略必须记录在事件中。

### 8.5 输出与背压

- 每条输出事件带 `seq`、`stream`、`offset` 和原始 bytes；
- 文本字段必须标明编码与替换错误；
- 每会话有内存、高水位、总输出和保留时长限制；
- 触发限制时发出 `output.truncated` 或 `resource.limit_hit`；
- 运行时不得静默丢弃输出；
- 客户端可按 cursor 续读，但 v0.1 不承诺跨进程重启恢复。

## 9. 协议草案

### 9.1 Transport

v0.1 使用 stdio 上的 JSON-RPC 2.0，原因是部署简单、不打开端口、容易被 Agent host 管理。

后续可增加当前用户可访问的 Windows Named Pipe 和 Unix Domain Socket。Transport 不改变方法语义。

### 9.2 方法

```text
runtime.hello
runtime.capabilities
session.start
session.get
session.subscribe
session.write
session.close_stdin
session.resize
session.interrupt
session.cancel
session.wait
session.dispose
screen.get          # optional capability
```

### 9.3 事件

```text
session.accepted
process.started
stdout
stderr
pty.output
screen.delta        # optional capability
shell.metadata      # advisory only
output.truncated
resource.limit_hit
process.exited
session.drained
session.completed
session.error
```

### 9.4 错误模型

错误至少包含：

```json
{
  "code": "EXECUTABLE_NOT_FOUND",
  "stage": "start",
  "message": "Executable could not be resolved",
  "retryable": false,
  "native": {
    "platform": "windows",
    "code": 2
  }
}
```

稳定错误码用于程序判断；本地化 message 用于人类阅读；native 信息用于诊断，不作为跨平台逻辑依据。

### 9.5 协议兼容

- `runtime.hello` 协商主版本和功能；
- 主版本变化允许破坏兼容；
- 同一主版本只新增可忽略字段和 capability；
- 未识别方法返回标准 method-not-found；
- 未支持能力返回 `CAPABILITY_NOT_SUPPORTED`，不得静默降级。

## 10. 系统架构

```text
                 +-----------------------+
                 | Agent / IDE / CLI     |
                 +-----------+-----------+
                             |
             +---------------v----------------+
             | ACP / MCP / CLI / SDK adapters |
             +---------------+----------------+
                             |
             +---------------v----------------+
             | Versioned Local Protocol       |
             +---------------+----------------+
                             |
          +------------------v-------------------+
          | Session Manager                      |
          | state | events | output | quota      |
          +------------------+-------------------+
                             |
             +---------------v----------------+
             | Target Router                  |
             +-----+-------------+------------+
                   |             |             |
            +------v------+ +----v-----+ +-----v----+
            | Windows     | | Linux    | | macOS    |
            | Pipe/ConPTY | | Pipe/PTY | | Pipe/PTY |
            | Job Object  | | PGID     | | PGID     |
            +------+------+ +----------+ +----------+
                   |
            +------v------+
            | WSL Adapter |
            +-------------+
```

### 10.1 模块建议

```text
runtime-core/       状态机、事件、配额、公共类型
protocol/           JSON-RPC schema、版本与兼容测试
backends/windows/   Pipe、ConPTY、Job Object、Win32 错误
backends/linux/     Pipe、PTY、process group
backends/macos/     Pipe、PTY、process group
backends/wsl/       Windows 到 WSL 的目标桥接
terminal/           VT parser、screen model（可选）
adapters/cli/       命令行入口
adapters/mcp/       MCP 映射
adapters/acp/       ACP 映射
fixtures/           跨平台测试程序
```

### 10.2 分层约束

- adapter 只做协议映射，不自行管理进程；
- backend 不了解 MCP/ACP；
- 状态机只消费标准 backend 事件；
- screen model 不参与完成判定；
- WSL 是独立 target，不伪装成普通 Windows executable。

## 11. Windows 实现重点

### 11.1 进程创建

- 使用 Unicode API 和 `CreateProcessW`；
- direct command 由专门模块序列化 argv；
- 使用 `STARTUPINFOEX` 与明确的 handle list；
- 仅继承所需句柄；
- 可执行文件解析、当前目录和环境块必须可审计；
- 启动失败必须在创建会话后及时返回结构化错误。

### 11.2 Pipe backend

- 为 stdin/stdout/stderr 建立独立管道；
- 父进程立即关闭不需要的管道端；
- 异步读取 stdout/stderr，避免互相阻塞；
- 主进程退出后继续排空，直至 EOF 或触发有记录的清理策略；
- 专门测试“孙进程错误继承写端导致永不 EOF”。

### 11.3 ConPTY backend

- 使用 `CreatePseudoConsole` 和 `ResizePseudoConsole`；
- 把 ConPTY 视为 UTF-8 + VT 的合并流；
- 不承诺恢复 stdout/stderr 来源；
- 独立管理伪控制台、管道和进程句柄的关闭顺序；
- 防止读取、退出和关闭之间的竞态。

### 11.4 Job Object

- 每个独立执行会话默认拥有 Job；
- 使用 kill-on-close 作为兜底回收机制；
- 记录无法加入 Job 的失败与兼容限制；
- Job 用于生命周期和资源控制，不宣称提供安全隔离。

### 11.5 WSL

v0.1 通过 `wsl.exe` 启动目标命令，并暴露发行版、用户和 cwd 等参数。Windows 侧 Job 能可靠约束 Windows 代理进程，但不必然覆盖所有 Linux 后代。

后续通过 WSL 内部轻量 supervisor 管理 Linux process group，提供更强的取消、退出和信号语义。

## 12. Linux 与 macOS 实现重点

- Pipe 模式优先使用 `posix_spawn` 或经过审计的 `fork/exec` 路径；
- PTY 模式使用 `posix_openpt`/`forkpty` 等平台接口；
- 每会话建立 process group，信号发送到 group；
- Linux 可选使用 cgroup 增强资源与进程树控制，但不作为基础协议假设；
- macOS 不假定存在 Linux cgroup；
- 使用同一组 fixtures 验证参数、Unicode、输出、取消和竞态；
- 原生差异通过 capability 和 native diagnostics 返回。

## 13. 安全与信任模型

### 13.1 v0.1 信任边界

- 本地、单用户、与调用者相同权限；
- 默认 stdio transport，不打开网络端口；
- 不自动提权；
- 不存储长期凭据；
- 不把执行结果上传到外部服务。

### 13.2 必须防护

- direct command 不经过 Shell；
- 明确 executable resolution，降低 PATH/cwd 劫持风险；
- 默认不记录完整环境变量和命令输出；
- 日志对 token、密码和常见 secret 模式做最小化处理；
- 只继承白名单句柄；
- 后续 Named Pipe/Unix Socket 仅允许当前用户访问；
- 配置输出、并发、内存、运行时间和进程数量上限；
- 协议输入做长度、类型和状态校验。

### 13.3 明确声明

本产品是执行运行时，不是安全沙箱。运行不可信代码需要额外使用 Windows Sandbox、AppContainer、容器、虚拟机或其他隔离机制。

## 14. 可观测性与诊断

`sharkrail doctor` 应输出：

- 运行时和协议版本；
- 操作系统、架构和后端 capability；
- ConPTY 可用性；
- 支持的 Shell 与 WSL 发行版；
- 当前资源限制；
- 精简且不泄露 secret 的自检结果。

可选结构化诊断包括：

- 会话状态迁移；
- 各阶段耗时；
- 读写字节数；
- 取消升级步骤；
- 被截断字节数；
- Win32/POSIX native error；
- 仍未关闭的句柄或任务计数（debug build）。

## 15. 测试策略

### 15.1 Fixtures

建立不依赖真实开发工具的测试程序：

```text
emit-bytes         输出指定字节与编码
echo-stdin         回显输入并测试 EOF
split-output       交错写 stdout/stderr
spawn-tree         创建多层子进程
hold-pipe-open     后代持续持有输出句柄
sleep              测试 timeout
ignore-interrupt   测试取消升级
crash              测试异常退出
argv-dump          输出收到的 argv
env-dump           输出筛选后的环境
vt-fixture         输出 VT、光标与颜色序列
```

### 15.2 测试维度

- Pipe / PTY / persistent terminal；
- Windows / WSL / Linux / macOS；
- direct / cmd / PowerShell / pwsh / bash / zsh；
- 空输出、小输出、大输出和持续输出；
- UTF-8、系统代码页、无效字节和二进制；
- 超长 argv、空参数、引号、反斜杠和空格路径；
- 正常退出、崩溃、超时、软中断、强杀；
- 子进程树、孤儿进程和错误句柄继承；
- 慢消费者、断连与重连；
- 并发会话和资源上限；
- 启动、读写、resize、取消和 dispose 竞态。

### 15.3 v0.1 验收标准

- 10,000 次短命令循环无句柄持续增长；
- 所有完成会话都产生且只产生一个 `session.completed`；
- `completed` 前已交付所有未截断输出；
- Pipe 模式稳定分离 stdout/stderr；
- 取消测试结束后没有 fixture 后代残留；
- 输出达到限制时有显式截断事件且内存保持有界；
- direct command 参数测试覆盖 Windows 引用边界；
- runtime 被强制关闭时，Windows Job 中的进程被回收；
- 协议兼容测试能验证未知字段、未知方法和缺失 capability；
- Windows CI 对每个合并请求运行核心一致性测试。

跨平台预览需要在 Linux/macOS CI 上通过同一套适用的一致性测试；无法达到相同保证的测试必须对应一个已声明的 capability 差异。

## 16. 成功指标

产品早期不以下载量作为唯一指标，而关注可验证的可靠性：

- Agent 执行失败中由运行时造成的超时/挂起比例；
- 取消后残留进程率；
- 输出丢失或静默截断率；
- Windows 与 POSIX 后端的一致性测试通过率；
- 接入新 Agent host 所需的 adapter 代码量；
- issue 中可复现案例的回归测试覆盖率；
- 在真实构建、测试、Git、REPL 和 TUI 场景中的成功率。

建议 v0.1 发布门槛：核心 fixtures 零已知残留进程、零静默输出丢失、零未分类完成状态。

## 17. 市场位置与差异化

当前生态大致分为四类：

| 类别 | 代表方向 | 已解决 | 留给本项目的机会 |
| --- | --- | --- | --- |
| 底层 PTY 库 | node-pty、portable-pty、pywinpty | 创建与驱动 PTY | Agent 生命周期、进程树、协议和一致性测试 |
| Terminal MCP | terminal-mcp 等 | 让 Agent 操作持久终端/TUI | Pipe-first 执行、Windows 边界和协议中立核心 |
| 终端/复用器 | Windows Terminal、Zellij、tmux 类工具 | 人类 UI、pane、session | Headless、可嵌入、机器可验证的完成语义 |
| Agent 自建执行器 | 各 Agent 内部 exec server | 满足单一产品需求 | 可复用、厂商中立、可独立测试的 runtime |

SharkRail 的差异化应集中在：

1. Windows 是一等公民，不是“能编译就算支持”；
2. 同时提供结构化 Pipe 执行和真实 PTY；
3. 以完成、取消、排空和进程树为核心契约；
4. 协议中立，MCP/ACP 只是 adapter；
5. 通过 capability 和 conformance suite 支持跨平台；
6. 可以作为库、sidecar 或 CLI 被不同 Agent 重用。

## 18. 发布路线图

### M0：产品重启

- 完成本产品文档、README、术语和 non-goals；
- 记录旧原型状态；
- 确认许可证与依赖政策；
- 建立 issue 模板、贡献指南和安全策略；
- 决定正式产品名与包名。

### M1：工程基础

- 选择实现语言与构建系统；
- 建立 `runtime-core` 状态机；
- 建立 Win32 句柄/进程 RAII 层；
- 建立 fixtures、Windows CI 和泄漏检测；
- 冻结 v0.1 协议草案。

### M2：Windows v0.1 alpha

- Pipe backend；
- ConPTY backend；
- Job Object 生命周期；
- CLI 与 stdio JSON-RPC；
- timeout/cancel/output limits；
- cmd/PowerShell/pwsh 和基础 WSL；
- `sharkrail doctor`。

### M3：跨平台预览

- Linux backend；
- macOS backend；
- capability matrix；
- 跨平台 conformance suite；
- 发布 adapter author guide。

### v0.2

- Windows Named Pipe / Unix Domain Socket；
- ARM64；
- 可选 VT screen model；
- WSL 内部 supervisor；
- 资源限制与诊断增强。

### v0.3

- MCP 与 ACP adapter；
- 首批语言 SDK 或稳定 C API；
- IDE/Agent host 集成样例；
- 性能和兼容基准。

### v1.0

- 稳定协议与兼容承诺；
- 公开、可重复的支持矩阵；
- 安全审查和故障注入验证；
- 至少两个独立 Agent/IDE 集成采用。

## 19. 尚待决定的问题

以下问题不阻塞产品定位，但应在 M1 前关闭：

1. **仓库迁移**：何时把历史仓库重命名为 `SharkRail`，是否保留旧 URL 的 GitHub redirect。
2. **实现语言**：现代 C++ 最贴近原项目和 Win32；Rust 更利于内存安全、跨平台抽象和单文件发布。需要用两个最小 spike 比较。
3. **许可证**：原项目为 GPL-3.0；若目标是被闭源 IDE/Agent 作为库嵌入，需要评估进程外 sidecar 模式是否足够，或由版权方决定是否采用更宽松/双许可证。
4. **API 粒度**：v0.1 是否只发布协议和 CLI，延后稳定 C API，以避免过早绑定 ABI。
5. **输出存储**：纯内存 ring buffer 还是超过阈值后落本地临时文件。
6. **资源治理**：哪些 Job/cgroup 限制默认启用，哪些只作为调用者策略。
7. **WSL 保证等级**：何时把 in-WSL supervisor 提升为默认组件。

## 20. 参考资料

- [Microsoft Learn: Pseudoconsoles](https://learn.microsoft.com/windows/console/pseudoconsoles)
- [Microsoft Learn: Creating a Pseudoconsole Session](https://learn.microsoft.com/windows/console/creating-a-pseudoconsole-session)
- [Microsoft Learn: Job Objects](https://learn.microsoft.com/windows/win32/procthread/job-objects)
- [Agent Client Protocol](https://agentclientprotocol.com/)
- [Model Context Protocol](https://modelcontextprotocol.io/)
- [microsoft/node-pty](https://github.com/microsoft/node-pty)
- [wezterm/portable-pty](https://github.com/wezterm/wezterm/tree/main/pty)
- [Terminal-MCP](https://github.com/adsamcik/Terminal-MCP)

---

本文件描述目标产品。具体 API 在进入实现后应拆分为版本化协议规范；架构决策应以 ADR 记录，不能只依赖本产品文档。
