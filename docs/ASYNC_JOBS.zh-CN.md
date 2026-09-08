# 可靠异步任务架构

状态：`Unreleased` 已提供实验性的单机实现；本文中的多机架构仍是设计方向。

该功能在现有运行时之上增加一个可选、自托管的 C/S 控制面。客户端声明期望状态，API
将它写入状态存储，多个可重入 Controller 持续比较期望状态与实际状态并执行调谐。客户端
得到 `job_id` 后即可断开，不需要关注执行连接和中间过程，任务结束后由 SharkRail 通知。
跨重启保留状态需要使用文件 SQLite 模式。

它不是工作流引擎或商业托管服务。一个 Job 只监督一个命令或交互会话；DAG、定时
调度、业务重试、凭据系统和沙箱供应仍属于上层系统或执行目标。

英文规范见 [ASYNC_JOBS.md](ASYNC_JOBS.md)。

## 当前实现边界

当前版本已经实现最有用的单机闭环：

| 当前已实现 | 明确尚未实现 |
| --- | --- |
| HTTP 提交、查询、结果、输出和取消 API | 多机调度与故障切换 |
| 必填幂等键与带 fencing 的 Attempt 所有权 | PostgreSQL 与 S3 兼容适配器 |
| 默认 SQLite 内存模式与持久化文件 SQLite | 独立 Executor Master 与进程池 |
| 有界本地输出文件 | 增量持久化输出流与 SSE |
| 一个 Control Master 监督一个可替换 Worker | 命令启动后的自动重试 |
| 有界请求、调谐、执行和通知并发 | 交互式 PTY Job 与远程 Executor |
| 事务 Outbox、HMAC 签名、重试与死信 | 工作流编排与多语言 SDK |

Worker 持有唯一的 SQLite 连接，并使用有界的角色线程。Master 同时检查 heartbeat 和
调谐进度；Worker 崩溃或失去进展时，Master 会按有界指数退避替换它，并尽力清理 Worker
最后上报的进程组。操作系统级进程树所有权仍是主要清理机制。

内存模式明确是易失模式。只有文件 SQLite 模式承诺已接受的资源记录能够跨服务重启保留。
输出在命令结束时一次性提交，尚不支持执行中的持久化分块流。下文涉及独立
Control/Executor Master、PostgreSQL、对象存储、多机 lease 或 SSE 的内容都是目标设计，
不是当前已经交付的能力。

## 目标与承诺

- SQLite 资源记录是在其存储生命周期内的事实源，应用内存队列不是；
- 文件 SQLite 模式下，只有任务持久化成功后才返回已接受；
- 相同幂等键的重复请求不会创建重复任务；
- 同一 Job 最多只有一个当前执行尝试能够更新结果；
- 每个已启动 Attempt 都进入明确终态或 `executor_lost`；
- 终态结果与通知意图在同一个事务中提交；
- Webhook 至少投递一次，接收方可以可靠去重；
- 文件 SQLite 模式下，服务重启不会丢失已接受的资源记录与待投递通知；中断的运行中
  Attempt 会进入 `executor_lost`；
- 输出丢失、Executor 丢失、重试和回调失败都必须显式可见。

Controller 承诺的是最终收敛，不是瞬时成功。每一个调谐动作都必须能够在崩溃后安全
重做；所有对外可见的状态更新都必须校验 revision 和 fencing。

系统不能承诺命令严格 Exactly Once。主机可能在启动进程后、持久化启动确认前宕机，
此时无法证明带外部副作用的命令是否已经执行。因此默认只允许启动前重试；启动后不
自动重试，除非调用方明确声明命令幂等。

## 当前单机架构

```text
Client --HTTP--> Control Master --> 集成 Worker
                                    |--> 有界请求线程
                                    |--> 调谐线程
                                    |--> 有界执行线程 --> SessionManager --> OS 进程
                                    |--> 通知线程 --> 签名 Webhook
                                    +--> SQLite JobStore + 本地 OutputStore
```

## 目标多机架构

```text
Client
  |
  | HTTP：声明期望状态、查询实际状态、请求取消
  v
Control Master
  |--> API Worker
  |--> Controller Worker
  +--> Notification Worker
  |
  | 校验 + compare-and-swap 事务
  v
JobStore（唯一事实源）
  | Resource · Attempt · Lease · Event · Outbox
  |
  +--> Job/Scheduler/Lease Reconciler --> Executor Master
  |                                           |
  |                                           +--> Executor Worker
  |                                                  |--> SessionManager
  |                                                  |--> OS Process / PTY
  |                                                  +--> OutputStore
  +--> Notification Reconciler --> 签名 Webhook --> Client
```

API 与 Controller 除 `JobStore` 外都不保存状态。Controller 之间不通过内存队列转移
任务所有权；所谓队列，只是对“尚未收敛的持久化资源”的查询。`SessionManager` 继续
作为进程生命周期的语义核心；新增 `JobManager` 负责资源校验和 Controller 协调，不
复制进程监督逻辑。

## 声明式资源模型

每个 Job 是一条持久化资源，包含不可变身份、期望状态和 Controller 维护的实际状态：

```json
{
  "metadata": {
    "id": "job_01K...",
    "tenant_id": "tenant_123",
    "generation": 1,
    "revision": 8,
    "created_at": "..."
  },
  "spec": {
    "command": ["pytest", "-q"],
    "cwd": "/workspace/project",
    "desired_state": "active",
    "timeout_seconds": 3600,
    "max_output_bytes": 16777216,
    "callback_endpoint_id": "build-system"
  },
  "status": {
    "observed_generation": 1,
    "phase": "running",
    "attempt_id": "attempt_01K...",
    "conditions": [
      {"type": "Accepted", "status": true, "reason": "Persisted"},
      {"type": "Ready", "status": false, "reason": "CommandRunning"}
    ]
  }
}
```

`spec` 表示调用方意图。Admission 完成后，执行字段不可修改；只有
`desired_state=cancelled` 等受支持的意图可以修改，并增加 `generation`。只有 Controller
能更新 `status`，所有写入都增加 `revision`，compare-and-swap 会拒绝旧写入。只有
`status.observed_generation == metadata.generation`，才表示 Controller 已处理当前意图。

Condition 使用稳定、机器可读的 `type`、`status`、`reason` 和状态切换时间，表达已接收、
已调度、运行中、输出降级、Executor 丢失、结果可用和通知已送达等事实，避免把所有组合
塞进一个含义模糊的 phase。

## 配置发现与安装

Server 默认读取一个系统级配置文件：

| 环境 | 生效配置 | 安装后的示例 |
| --- | --- | --- |
| Linux 与其他 Unix 服务 | `/etc/sharkrail/sharkrail.toml` | `/etc/sharkrail/sharkrail.toml.example` |
| Windows 系统服务 | `%ProgramData%\SharkRail\sharkrail.toml` | `%ProgramData%\SharkRail\sharkrail.toml.example` |
| 容器 | `/etc/sharkrail/sharkrail.toml` | 包含在镜像与源码发行包中 |

Windows 路径遵循 [Docker Engine](https://docs.docker.com/engine/daemon/) 和
[Git for Windows](https://git-scm.com/book/en/v2/Getting-Started-First-Time-Git-Setup)
等系统服务软件的惯例。实现必须通过 Windows Known Folder API 解析
`FOLDERID_ProgramData`，不能假定系统盘一定是 `C:`。系统服务不会隐式读取登录用户的
`%APPDATA%`，因为服务账号可能与安装用户不同。

配置路径优先级为：`--config <path>`、`SHARKRAIL_CONFIG_FILE`、平台系统路径。隐式路径
不存在属于合法情况，此时使用内置默认值；显式指定的文件不存在、TOML 无效、包含未知
字段、配置冲突、权限不安全或值非法时，必须带精确错误拒绝启动，不能误判成“未配置
数据库”。

配置项优先级为：命令行参数、环境变量、配置文件、内置默认值。`sharkrail config show`
显示最终选中的文件、非敏感有效配置以及每一项的来源；`sharkrail config validate` 只做
校验，不启动服务。

每一种异步服务发行物都必须包含
[`configs/sharkrail.toml.example`](../configs/sharkrail.toml.example)。原生系统包和
Windows Installer 会把副本放到上述示例路径，但不能覆盖已有文件。Python wheel 不能
安全写入需要管理员权限的系统目录，因此把相同文件嵌入包中，并提供：

```text
sharkrail config sample
sharkrail config init --system
```

`sample` 输出到 stdout；`init --system` 以原子方式复制到平台系统路径，需要时使用平台
正常提权流程，并且除非显式传入 `--force`，否则拒绝覆盖已有文件。卸载软件不能删除已被
操作人员修改的生效配置。

## 可配置持久层

持久层拆成两个独立接口：

- `JobStore`：事务保存 Resource、Attempt、Lease、revision、持久事件与回调 Outbox；
- `OutputStore`：保存 stdout/stderr 正文与 checksum。

没有配置数据库 URL 时，SharkRail 使用 SQLite 内存模式启动。这是可用性优先的零配置
模式：

```text
SHARKRAIL_JOB_STORE_URL=sqlite:///:memory:
SHARKRAIL_OUTPUT_STORE_URL=file://<运行时目录>/output
```

命令输出先按硬字节上限在内存中捕获，命令结束后再写入本地 OutputStore；当前尚未实现
增量持久化输出。易失运行时目录只属于当前服务实例，重启后可以被删除。需要单机持久化时
配置 SQLite：

```text
SHARKRAIL_STATE_DIR=/var/lib/sharkrail
SHARKRAIL_JOB_STORE_URL=sqlite:////var/lib/sharkrail/sharkrail.db
SHARKRAIL_OUTPUT_STORE_URL=file:///var/lib/sharkrail/output
```

未来多节点部署预计使用 PostgreSQL 和 S3 兼容对象存储；当前版本不接受这些 URL：

```text
SHARKRAIL_JOB_STORE_URL=postgresql://user:password@db/sharkrail
SHARKRAIL_OUTPUT_STORE_URL=s3://sharkrail-output/jobs
```

Windows 上相对 SQLite 或文件 URL 基于 `%ProgramData%\SharkRail\data` 解析；Unix 上
基于 `SHARKRAIL_STATE_DIR` 解析，其系统服务默认值为 `/var/lib/sharkrail`。同时支持
`SHARKRAIL_JOB_STORE_URL_FILE` 读取挂载为文件的 Secret，它与直接 URL 互斥。遇到未知
scheme 必须拒绝启动。只有通过相同的事务、revision、lease、migration、崩溃恢复和
Outbox 一致性测试，适配器才能被标记为受支持；能连接数据库不代表已经获得可靠性保证。

SQLite 是本地和单实例场景推荐的持久化方案，不是多节点数据库。应启用 foreign key、
WAL、busy timeout、显式事务和有文档说明的 durability 级别，并提供 migration 与备份。
数据库和输出目录必须位于持久磁盘。禁止多个 Server 实例通过 NFS、SMB 或其他网络文件
系统共享同一个 SQLite 文件。

### 易失 SQLite 内存模式

SQLite 内存模式让程序在没有数据库配置时仍可使用，但它属于明确的降级持久性等级：

- SQLite State Worker 或宿主退出后，Job 状态、幂等键、lease、状态历史和待投递回调
  全部丢失；
- 不承诺重启恢复、多实例 ownership、持久回调和已接收 Job 的持久性；
- Job 数量、metadata 字节、事件历史、TTL 和临时输出字节都有上限，过载时返回 `429`，
  不能冒险触发 OOM；
- 提交和状态响应包含 `"durability": "volatile"`，启动日志输出一次醒目警告，
  `/health/state` 报告 `DEGRADED_VOLATILE_STORE`；
- 需要跨服务重启保留状态的调用方必须配置文件 SQLite。

集成 Worker 持有唯一权威的内存 SQLite 连接，其有界角色线程共享该连接。Worker 每次
启动生成新的 `store_epoch`。Worker 故障时，Master 根据最后一次 heartbeat 上报的信息
尽力清理进程组，然后再启动拥有全新空状态和 epoch 的 Worker。

数据库配置缺失时选择 SQLite 内存模式；显式配置 `sqlite:///:memory:` 表示主动选择。
相反，已经配置的文件 SQLite 无法使用时绝不能自动回退内存：Worker 启动失败，Master
执行有界重启策略。静默回退会产生状态分裂和假成功。

不采用 H2。H2 很适合 JVM 应用，但在 Python 运行时中会额外引入 JVM、JDBC 集成、用于
跨进程访问的数据库 Server 进程和第二套运维工具链，却不会改善内存数据的易失性。
SQLite 内存模式无需新增运行时依赖，同时保留 SQL 事务、约束，并尽可能复用文件 SQLite
的 schema 与 migration 路径；后端无关的一致性测试继续覆盖方言差异。

## 部署可移植性

同一套架构必须能够部署在裸金属机、虚拟机或容器中。打包方式可以不同，但持久化和
恢复契约保持一致：

- 零配置安装使用有界易失内存状态；需要单机持久化时使用 SQLite，并把输出写入持久化
  主机目录；
- 容器必须把 `SHARKRAIL_STATE_DIR` 挂载到持久化主机目录或数据卷，因为容器可写层可能
  被替换；
- 升级期间必须避免两个 Server 实例同时打开同一个 SQLite 数据库；
- 多 API/Controller 实例或跨主机 Executor 必须使用共享的 PostgreSQL `JobStore`，大
  体积输出建议使用 S3 兼容对象存储；
- 无论使用哪一种进程管理器或容器运行时，都可以通过
  `SHARKRAIL_JOB_STORE_URL_FILE` 提供数据库凭据。

文件在进程或机器重启后仍然存在，以及 Controller 能恢复逻辑状态，是两个独立要求。
每一种部署形态都需要经过验证的数据库备份、输出保留、重启流程和恢复演练。

## 控制面进程可靠性

只有持久化数据库还不足以保证控制面可靠。设计必须在过载前主动保护，在故障发生后隔离
影响，由外部拉起进程，并根据持久化状态重建全部工作。已经接受的 Job 不能依赖某一个
API 或 Controller 进程一直存活。

### Master 与 Worker 进程模型

生产环境使用一个轻量 Master 进程和一组可替换 Worker 进程。Master 不接收业务请求、
不拥有 Job、不调度命令，也不保存权威状态；它只负责启动 Worker、监控进展、优雅下线、
替换异常或失去响应的 Worker、聚合进程健康状态和协调停止。Master 不承担业务工作，
可以显著缩小内存占用与故障面。

Worker 按职责分池：

```text
Control Master
  |-- API Worker Pool
  |-- Controller Worker Pool
  +-- Notification Worker Pool

Executor Master
  +-- Executor Worker Pool
        +-- 受控命令进程树
```

Control Master 与 Executor Master 是两个独立故障域，因此控制面过载不能直接终止正在运行
的命令。小型部署可以由同一个可执行文件启动两棵服务进程树，但它们仍应使用独立 Master、
进程组、资源预算和停止策略。命令进程和输出读取任务绝不能运行在 API 或 Controller
Worker 内。

跨平台实现使用 spawn 语义创建 Worker，不能依赖 Unix 专有的 fork 行为。Master 维护
固定、有上限且可配置的 Worker 数量，不能为每个请求创建新进程。API 监听端口的持有与
交接必须使用一种明确的跨平台策略，保证替换 Worker 时不会丢失已接受请求，也不会让两个
Worker 写入同一个响应。

每个 Worker 通过有界本地 IPC 上报单调进展 heartbeat。Worker 退出、超过进展期限、突破
硬资源上限或有限自检失败时，Master 将其替换。Master 先把 Worker 标记为 unready 并请求
优雅 drain；超过期限后终止 Worker 及其拥有的子进程树。替换使用带随机抖动的指数退避和
每角色重启频率限制。某个角色连续崩溃时打开熔断器并报告 degraded，不能形成 fork storm。

Worker 可随时丢弃，因此替换过程必须安全。新 Worker 使用新的 `worker_id` 和 `boot_id`，
不能继承前任的 ownership，只能从 `JobStore` 重新领取工作；所有 claim 和写入继续接受
lease、revision 与 fencing 校验。计划升级时启动新一代 Worker，等待 ready 后再 drain
旧一代，并在整个切换期间为控制流量保留容量。

Master 自身仍由宿主服务管理器或容器运行时等外部 Supervisor 管理。外部 Supervisor
负责自动重启、带随机抖动的指数退避、重启频率限制和上次退出诊断。Master 无法从自身
OOM Kill、死锁、运行时故障或整机重启中自救。Master 异常退出后，存活 Worker 必须按照
明确的平台契约被终止或重新接管，不能成为长期无人监督的孤儿进程。

Control Master 不可用期间，由独立服务监督的 Executor 继续运行已领取命令，在可能的
情况下持久化有界输出并重试状态上报。如果部署者主动把两棵服务进程树放在同一故障域，
则接受共享故障可能产生 `executor_lost` 的较弱保证。

每次 Master 启动生成 `instance_id`，每次 Worker 启动生成 `worker_id` 和 `boot_id`。
持久化 claim 同时记录这些身份、lease 期限和 epoch；旧 Worker 或暂停后恢复的 Worker
写入会被 revision 与 fencing 校验拒绝。

### 启动、停止与恢复

只有配置有效、schema 版本兼容、`JobStore` 可写且已经获得角色所需 ownership 后，启动
才算成功。Schema migration 使用数据库锁，并作为独立管理操作执行；普通副本不能并发
抢着升级。服务暴露：

```text
GET /health/live   进程事件循环和 watchdog 仍在推进
GET /health/ready  当前角色能够安全接受对应流量
GET /health/state  依赖、队列、lease 和调谐诊断
```

不能因为数据库短暂不可用就让 liveness 失败，否则会形成重启风暴。角色无法安全服务时
readiness 失败；普通过载和依赖故障通过诊断状态明确报告。

优雅停止时，API 先停止接收新 Job，Controller 停止领取新工作，未完成数据库事务在期限
内结束，已经持有的 lease 主动释放或自然过期。异常退出时，数据库事务必须原子回滚。
重启后 Controller 先执行 full resync，再开始正常调度，并调谐未完成 Job、过期 claim 和
待投递 Outbox；正确性不依赖任何内存 checkpoint。

SQLite 模式只允许一个控制面实例，并使用独占进程锁阻止意外重复启动。它提供的是可靠
重启恢复，而不是服务永不中断。多实例高可用需要 PostgreSQL：API 副本无状态，多个
Controller 通过数据库短 claim 主动分担任务；确实只能单实例执行的维护操作使用可续租
lease 和 fencing epoch。分布式 ownership 不能依赖本地 mutex。

### 过载保护

控制面必须在内存、文件描述符、线程或数据库连接耗尽前丢弃过量负载：

- 限制 HTTP 连接数、请求体、请求时长和每租户提交速率；
- 数据库连接池保持固定且较小，事务要短，等待连接必须有上限；
- 限制每个 Controller 工作队列、resync batch、回调 batch、重试集合和并发异步任务数；
- 输出直接流式写入 `OutputStore`，API/Controller 内存不能累积命令输出或完整结果集；
- 为提交、状态/取消、Executor heartbeat/结果更新和通知投递分别预留并发预算；
- heartbeat、取消和完成写入优先于新 Job，避免过载掩盖正在运行的任务；
- 使用全局与租户配额及公平调度，避免一个调用方饿死恢复流量或其他租户。

Admission 容量满时，新提交返回带 `Retry-After` 的 `429 Too Many Requests`；必要依赖
不可用时返回 `503 Service Unavailable`。必须在产生部分状态前拒绝请求。已经接受的 Job
保持持久化，恢复后继续调谐。状态查询和取消使用预留容量，不能与新提交共享无界队列。

异常隔离到单个请求和 Job。格式错误或反复失败的 Job 写入稳定 Failure Condition，并在
有限次数后隔离；它不能使整个 Controller 循环崩溃，也不能形成高频重试。进程连续崩溃
时，Supervisor 执行退避并暴露 degraded 状态，不能立即无限重启。

### 故障检测与可靠性测试

至少监控进程 RSS、CPU、文件描述符、event-loop lag、线程数、数据库连接等待、请求拒绝、
工作队列深度、调谐延迟、最老 lease、Outbox 年龄、重启次数，以及每个 Controller 距离
上次取得进展的时间。Watchdog 可以在记录有界诊断后终止死锁进程，再由外部 Supervisor
重新拉起。

发布前必须通过自动化故障注入证明：

| 故障注入 | 必须提供的证据 |
| --- | --- |
| Admission 期间杀死 API/Controller | 没有未持久化 Job 获得 `202`；已提交 Job 不丢失 |
| 杀死或卡死一个 Worker | Master 只替换该 Worker，持久化任务会被重新领取 |
| 杀死 Control Master | 外部 Supervisor 将其拉起，不产生永久孤儿 Worker，full resync 能收敛 |
| 让 Worker 连续崩溃 | 每角色退避与熔断器阻止 fork storm |
| 外部动作完成但 status 写入前杀死 Controller | 调谐动作幂等，或明确报告执行不确定性 |
| 提交流量打满 | 新任务获得有界 `429`；状态、取消和 heartbeat 仍能推进 |
| 输出速率打满 | 内存保持有界，输出截断或降级显式可见 |
| 数据库变慢或断开 | 停止 admission，事务有界，恢复时不会形成惊群 |
| Notification Worker 连续崩溃 | Job 结果不变，Outbox 恢复且不丢失 |
| 启动第二个 SQLite 控制面 | 在可能破坏 ownership 前拒绝启动 |
| 重启全部控制面角色 | full resync 无需人工修复即可收敛 Job、lease 和通知 |

## 客户端 API

提交任务：

```http
POST /v1/jobs
Authorization: Bearer <token>
Idempotency-Key: <caller-generated-key>
Content-Type: application/json
```

```json
{
  "command": ["pytest", "-q"],
  "cwd": "/workspace/project",
  "timeout_seconds": 3600,
  "max_output_bytes": 16777216,
  "callback": {"endpoint_id": "build-system"}
}
```

只有 Job 和不可变请求摘要写入数据库后才返回：

```json
{
  "job_id": "job_01K...",
  "status": "queued",
  "revision": 1,
  "status_url": "/v1/jobs/job_01K..."
}
```

幂等唯一键为 `(tenant_id, idempotency_key)`。相同内容重复提交时返回原 Job；相同键
但内容不同则返回 `409 Conflict`。

查询和控制接口：

```http
GET  /v1/jobs/{job_id}
GET  /v1/jobs/{job_id}/result
GET  /v1/jobs/{job_id}/output?stream=stdout&cursor=...
POST /v1/jobs/{job_id}/cancel
```

查询接口始终保留，作为 Webhook 投递失败时的兜底。可以为操作人员或交互客户端增加
SSE，但业务客户端不应依赖长连接。
取消接口只把资源意图更新为 `desired_state=cancelled`；它不能绕过调谐循环直接向进程
发信号，并在实际状态尚未收敛时错误地报告成功。

## Job 与 Attempt 状态

业务 Job 状态与进程内 Session 状态分离：

```text
queued -> assigned -> running -> succeeded
                            +--> failed
                            +--> timed_out
                            +--> canceled
                            +--> executor_lost
queued/assigned ----------------> expired
```

Job 可以在策略允许时包含多个 Attempt：

```text
Job
  +-- Attempt 1: executor_lost
  +-- Attempt 2: succeeded
```

终态不可修改。回调投递使用独立 Condition，通知失败不能把已经成功的命令改成失败。
事件使用唯一 `event_id` 和 Job 内单调递增序号。

## 调谐循环

每个 Controller 重复执行同一种逻辑：列出或 watch 尚未收敛的资源，计算一个有界的下一
步动作，带 revision 前置条件写入，然后继续调谐。调谐必须以当前状态为依据，而不是依赖
某个事件只出现一次；事件漏掉或重复只影响延迟，不能影响正确性。

```python
async def reconcile(job_id: str) -> None:
    job = await store.get(job_id)
    if job.status.phase in TERMINAL_PHASES:
        await ensure_result_and_notification(job)
    elif job.spec.desired_state == "cancelled":
        await ensure_cancelled(job)
    elif not job.status.attempt_id:
        await ensure_attempt_assigned(job)
    else:
        await ensure_attempt_observed(job)
```

所有 `ensure_*` 操作都必须基于不可变 Resource ID 或 Attempt ID 实现幂等。持久事件日志
只是唤醒优化和审计记录；周期性 full resync 负责修复漏事件。Controller 使用有界工作
队列、指数退避和随机抖动。永久无效的资源写入说明原因的 Condition，不能进入无休止热
循环。

各 Controller 只承担单一职责：

- Admission 校验策略，并在一个事务中创建初始资源；
- Scheduler 选择符合条件的 Executor，并创建一个带 fencing 的 Attempt；
- Execution 观察 `SessionManager`，持久化进度或终态结果；
- Lease Reconciler 检测失联 Executor，并应用显式重试策略；
- Notification 将终态结果与 Outbox 投递状态调谐一致；
- Retention 在最终删除资源前清理输出并记录过期事实。

删除 Job 时先设置 deletion timestamp。Retention finalizer 会阻止记录立即消失，直到进程
终止、输出保留策略完成且必要审计事实已经持久化。Finalizer 必须有操作人员可见的超时
和恢复流程，避免清理故障让资源永远无法删除。

## Lease、Heartbeat 与 Fencing

Scheduler Controller 在一个事务中领取需要调谐的任务，并记录：

```text
executor_id
lease_expires_at
lease_epoch
```

Executor 必须在 lease 过期前发送 heartbeat。每次重新分配都增加 `lease_epoch`，所有
Executor 写入必须同时校验 epoch 和 Job revision。旧 Executor 即使恢复，也不能覆盖
当前所有者的结果。数据库必须约束同一 Job 最多只有一个 active Attempt。

Attempt ID 也作为 operation token 传给 Executor。同一个 assignment 被重复投递时，必须
观察或恢复同一个本地操作，不能启动第二个进程。由于进程创建与持久化确认无法组成一个
跨系统事务，主机故障后仍需明确报告不确定性，不能宣称 Exactly Once。

队列需要全局、租户、Executor 和策略容量限制。过载时返回 `429` 或 `503`，不能无限
积压。超过最大排队时间的任务进入 `expired` 并产生终态通知。

## 故障与重启语义

| 故障 | 必须实现的行为 |
| --- | --- |
| Client 断线 | 不影响已经接受的 Job |
| API 重启 | 从持久化 `JobStore` 恢复，不丢任务 |
| Scheduler 重启 | 对过期 lease 执行 reconciliation |
| Controller 漏掉事件 | 周期性 full resync 发现状态偏差 |
| 重复调谐 | 幂等动作与 revision 校验阻止重复状态更新 |
| 通知进程重启 | 继续处理未完成的 Outbox 记录 |
| Executor 控制进程丢失 | 清理受控进程树，Attempt 进入 `executor_lost` |
| Executor 主机宕机 | lease 过期后进入 `executor_lost` |
| 数据库不可用 | 停止接收和分配，不执行未记录的任务 |

第一版不承诺 Executor 重启后重新接管任意存活进程。跨平台恢复 pipe、PTY、Job
Object、process group 和字节 offset 并不可靠。更可信的行为是清理进程树、报告
`executor_lost`，然后严格按照显式重试策略处理。

OS 进程创建和持久化启动确认之间存在不可消除的崩溃窗口。系统必须在启动进程前创建
Attempt，使用 fencing token，并将不确定性明确报告，不能静默重复执行可能有副作用的
命令。

## 增量持久化输出（目标扩展）

长任务输出不能只保存在 `SessionManager` 内存中。新增输出存储接口：

```python
class OutputStore(Protocol):
    async def append(self, job_id, attempt_id, stream, offset, data): ...
    async def finalize(self, job_id, attempt_id): ...
```

输出正文放在文件或对象存储；数据库只保存 Job/Attempt、stream、绝对 offset、长度、
checksum、storage key、保留区间和丢弃字节数。继续保持现有原始字节、单调 offset、
显式截断和 drain 完成后才进入终态的契约。

最终结果包含输出大小、截断事实、checksum 和授权后的结果地址。输出过期必须显式可见，
并与 Job 结果的保留期限分开管理。

## 事务 Outbox 与 Webhook

Attempt 进入终态时，在同一个数据库事务中更新 Job 并写入通知：

```sql
BEGIN;
UPDATE jobs SET status = 'succeeded', revision = revision + 1 ...;
INSERT INTO callback_outbox (...);
COMMIT;
```

这能避免“结果已保存，但进程在发送通知前崩溃”造成永久漏通知。Dispatcher 通过 lease
领取 Outbox，并至少投递一次。建议退避为：立即、5 秒、30 秒、2 分钟、10 分钟、
1 小时，并加入随机抖动。`429` 遵循 `Retry-After`；超时、网络错误和 `5xx` 重试；
永久 `4xx` 进入死信状态，并允许管理员检查与重放。

终态回调示例：

```json
{
  "event_id": "evt_01K...",
  "event_type": "job.completed",
  "job_id": "job_01K...",
  "revision": 8,
  "attempt": 1,
  "status": "succeeded",
  "exit_code": 0,
  "started_at": "...",
  "completed_at": "...",
  "result_url": "https://rail.example/v1/jobs/job_01K.../result",
  "output": {
    "stdout_bytes": 18230,
    "stderr_bytes": 320,
    "truncated": false,
    "sha256": "..."
  }
}
```

使用 HMAC-SHA256 对时间戳和原始 body 签名，并在 Header 中携带 `event_id`。接收方按
`event_id` 去重，且只有在自己的事务持久化成功后才返回 `2xx`。

## 安全边界

普通调用方不应提交任意 `callback_url`，而应引用管理员预先登记的 `endpoint_id`。
登记时检查协议、主机、端口、DNS、重定向和租户归属；生产投递必须阻止 loopback、
link-local、云元数据地址、未授权私网地址和 DNS rebinding。

零配置回环模式明确不启用认证，并把所有请求固定为单一租户 `default`；它只适合本地开发。
任何生产部署或同机 TLS 代理都必须配置互不相同的租户 Bearer 凭据和独立管理员 token。
系统还需要：

- 配置认证后由凭据绑定租户身份，并执行 Job 所有权检查；
- 调用方无法放宽的宿主执行策略；
- command、cwd、env、运行时间、输出和并发限制；
- 回调密钥与凭据引用加密；
- HMAC 或 mTLS 回调认证；
- 脱敏日志和有界审计记录；
- 对不可信命令使用容器、VM 或其他 sandbox target；
- 明确的结果和输出 TTL。

SharkRail 是执行监督器，不是安全沙箱。

## 持久化模型

最小数据模型：

- `jobs`：metadata、不可变 spec、status、Condition、generation、revision、deletion
  timestamp、finalizer、请求摘要和策略；
- `job_attempts`：Executor、lease epoch、时间和结果；
- `executor_nodes`：容量、能力、heartbeat 和 drain 状态；
- `resource_events`：用于唤醒和审计的单调变更序号；
- `output_objects`：字节区间、hash、保留策略和 storage key；
- `callback_endpoints`：租户目标地址和密钥引用；
- `callback_outbox`：不可变通知事件和投递状态；
- `callback_deliveries`：投递历史、响应类别和下次重试时间；
- `audit_events`：有界的管理与安全审计。

数据库必须强制幂等键唯一、event ID 唯一、每个 Job 一个 active Attempt、合法状态迁移、
Resource 与 Event 原子写入、revision compare-and-swap 和 fencing epoch 校验，不能只依赖
应用层判断。Event 压缩必须保留安全 revision 水位；落后于水位的 watcher 必须执行 full
resync，不能猜测遗漏了哪些事件。

## 可观测性与可靠性指标

至少暴露：队列年龄、拒绝数量、调度延迟、active/expired lease、Executor heartbeat
年龄、运行耗时、终态结果、输出和截断字节、Outbox 年龄、回调次数与延迟、死信数量。
日志使用 `tenant_id`、`job_id`、`attempt_id`、`executor_id`、`trace_id` 和
`event_id` 关联，但不得记录密钥。

初始目标应可测量，而不是承诺绝对可靠：

- 已确认接受但未进入持久化存储的 Job 为零；
- 没有 Outbox 事件的终态结果为零；
- 一致性测试中，同一 Job 的并发 active Attempt 为零；
- Executor lease 过期检测时间有明确上界；
- 公开回调投递延迟分位数和死信率。

## 交付状态与后续顺序

### 已交付的实验性单机子集

- REST 提交、查询、输出、结果和取消接口；
- 系统配置发现、validate/show/init 命令和安装后的示例配置；
- 一个 Control Master 和一个集成 Worker、有界角色线程、heartbeat/进度检查、drain、
  替换与重启退避；
- `JobStore`/`OutputStore` 接口、有界 SQLite 内存/临时文件默认实现、持久化文件 SQLite、
  migration、备份与幂等 admission；
- 声明式 `spec`/`status`、revision 校验、持久事件、Condition 与状态驱动调谐；
- 一组 Controller 和一个 Executor，复用现有 `SessionManager`；
- 本地文件 `OutputStore`；
- 事务 Outbox、签名 Webhook、重试和死信；
- Master/Worker 启动与重启上界、重复提交、所有权 fencing、回调签名、取消、超时、
  过载限制、持久化重启恢复和实例锁回归测试。

单机模式仍需继续加强：增量输出持久化、各平台孤儿进程故障注入、磁盘满/损坏测试、
周期性 full resync 指标，以及独立 Executor 进程隔离。

### 第二阶段：多 Executor 可靠性

- PostgreSQL `JobStore`、S3 兼容 `OutputStore` 和后端一致性测试；
- 多 Control Master、active-active Controller Worker、滚动 Worker generation 和带
  fencing 的单例维护操作；
- Executor 注册、能力匹配、lease、heartbeat 和 fencing；
- 处理过期 lease 和不确定 Attempt 的 reconciler；
- 租户配额、公平调度、队列期限和节点 draining；
- failover、网络分区和重复投递一致性测试。

### 第三阶段：可选适配器

- SSE/operator 事件流和语言 SDK；
- 外部 sandbox 与 execution target 适配器；
- 有界 soak 测试和公开可靠性证据；
- 仅当数据库 Outbox 吞吐成为实测瓶颈时增加消息队列适配器。

从实验功能升级为支持契约之前，每一个状态迁移都必须有故障注入测试，Windows、Linux 和
macOS 必须有真实进程泄漏测试，并公开恢复证据。代码完成不等于可靠性已经得到证明。
