# 可靠异步任务架构

状态：设计提案；SharkRail v0.1 尚未实现。

本方案在现有运行时之上增加一个可选、自托管的 C/S 控制面，并借鉴 Kubernetes 的
状态管理模式：客户端声明期望状态，API 将它持久化，多个可重入 Controller 持续比较
期望状态与实际状态并执行调谐。客户端得到持久化 `job_id` 后即可断开，不需要关注执行
连接和中间过程，任务结束后由 SharkRail 可靠通知。

它不是工作流引擎或商业托管服务。一个 Job 只监督一个命令或交互会话；DAG、定时
调度、业务重试、凭据系统和沙箱供应仍属于上层系统或执行目标。

英文规范见 [ASYNC_JOBS.md](ASYNC_JOBS.md)。

## 目标与承诺

- 持久化资源记录是真实来源，内存队列不是；
- 只有任务持久化成功后才返回已接受；
- 相同幂等键的重复请求不会创建重复任务；
- 同一 Job 最多只有一个当前执行尝试能够更新结果；
- 每个已启动 Attempt 都进入明确终态或 `executor_lost`；
- 终态结果与通知意图在同一个事务中提交；
- Webhook 至少投递一次，接收方可以可靠去重；
- Client、API、Scheduler 和通知进程重启不会丢失已接受任务；
- 输出丢失、Executor 丢失、重试和回调失败都必须显式可见。

与 Kubernetes 一样，Controller 承诺的是最终收敛，不是瞬时成功。每一个调谐动作都
必须能够在崩溃后安全重做；所有对外可见的状态更新都必须校验 revision 和 fencing。

系统不能承诺命令严格 Exactly Once。主机可能在启动进程后、持久化启动确认前宕机，
此时无法证明带外部副作用的命令是否已经执行。因此默认只允许启动前重试；启动后不
自动重试，除非调用方明确声明命令幂等。

## 总体架构

```text
Client
  |
  | HTTP：声明期望状态、查询实际状态、请求取消
  v
API Server
  |
  | 校验 + compare-and-swap 事务
  v
JobStore（唯一事实源）
  | Resource · Attempt · Lease · Event · Outbox
  |
  +--> Job Controller --------+
  +--> Scheduler Controller --+--> Executor Agent --> SessionManager
  +--> Lease Reconciler ------+                         |--> OS Process / PTY
  +--> Notification Controller                         +--> OutputStore
  +--> Retention Controller
             |
             +--> 签名 Webhook --> Client
```

API 与 Controller 除 `JobStore` 外都不保存状态。Controller 之间不通过内存队列转移
任务所有权；所谓队列，只是对“尚未收敛的持久化资源”的查询。`SessionManager` 继续
作为进程生命周期的语义核心；新增 `JobManager` 负责资源校验和 Controller 协调，不
复制进程监督逻辑。

Kubernetes 概念与 SharkRail 的对应关系：

| Kubernetes | SharkRail |
| --- | --- |
| API Server | Job API |
| etcd | `JobStore`（默认 SQLite，多节点使用 PostgreSQL） |
| Resource 的 `spec` / `status` | Job 期望状态 / 实际状态 |
| Controller reconciliation loop | Job、Lease、通知和保留期 Reconciler |
| Scheduler | Executor 调度 Controller |
| kubelet | Executor Agent 与 `SessionManager` |
| `resourceVersion` / `generation` | revision / 期望状态 generation |
| Pod lease 与 UID | Attempt lease、epoch 与不可变 Attempt ID |

这里借鉴的是架构，不依赖 Kubernetes。SharkRail 不能把 Kubernetes 控制面的 etcd、
ConfigMap、Secret 或 CRD 当成自己的 Job 数据库。

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
    "retry_policy": {"before_start": 3, "after_start": 0},
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

## 可配置持久层

持久层拆成两个独立接口：

- `JobStore`：事务保存 Resource、Attempt、Lease、revision、持久事件与回调 Outbox；
- `OutputStore`：保存 stdout/stderr 正文与 checksum。

零配置默认使用 SQLite 和本地文件。配置优先级为：命令行参数、环境变量、配置文件、
默认值：

```text
SHARKRAIL_STATE_DIR=<操作系统用户状态目录>/sharkrail
SHARKRAIL_JOB_STORE_URL=sqlite:///sharkrail.db
SHARKRAIL_OUTPUT_STORE_URL=file://./output
```

相对路径统一基于 `SHARKRAIL_STATE_DIR` 解析。单节点服务器可以配置绝对路径：

```text
SHARKRAIL_STATE_DIR=/var/lib/sharkrail
SHARKRAIL_JOB_STORE_URL=sqlite:////var/lib/sharkrail/sharkrail.db
SHARKRAIL_OUTPUT_STORE_URL=file:///var/lib/sharkrail/output
```

多节点部署使用受支持的适配器，第一批提供 PostgreSQL 和 S3 兼容对象存储：

```text
SHARKRAIL_JOB_STORE_URL=postgresql://user:password@db/sharkrail
SHARKRAIL_OUTPUT_STORE_URL=s3://sharkrail-output/jobs
```

同时支持 `SHARKRAIL_JOB_STORE_URL_FILE`，用于读取挂载为文件的 Secret；它与直接设置
URL 的环境变量互斥。遇到未知 scheme 必须拒绝启动。只有通过相同的事务、revision、
lease、migration、崩溃恢复和 Outbox 一致性测试，适配器才能被标记为受支持；能连接某种
数据库不代表已经获得可靠性保证。

SQLite 是正式支持的本地、单实例默认方案，不是多节点数据库。应启用 foreign key、
WAL、busy timeout、显式事务和有文档说明的 durability 级别，并提供 migration 与备份。
数据库和输出目录必须位于持久磁盘。禁止多个 Server Pod 通过 NFS、SMB 或多写卷共享
同一个 SQLite 文件。

## 部署在 Kubernetes 上

Kubernetes 使用 etcd 保存自己的集群状态，但 SharkRail 的业务状态仍然保存在
`JobStore`。Pod 可写层随时可能丢失。SQLite 单副本部署需要把 CSI 提供的 PVC 挂载到
`/var/lib/sharkrail`，尽可能使用 `ReadWriteOncePod`，并保证滚动升级时新旧实例不会同时
访问数据库。StatefulSet 能提供稳定 Pod 身份和卷绑定，但不会自动让 SQLite 具备高可用；
仍然需要卷快照和恢复演练。

如果 API/Controller 有多个副本，或 Executor 分布在多个节点，应使用外部托管或 Operator
管理的 PostgreSQL，并把大体积输出写入 S3 兼容对象存储。数据库连接信息通过挂载的
Secret 和 `SHARKRAIL_JOB_STORE_URL_FILE` 提供。PV 解决文件存活问题，Controller 和
Lease 解决逻辑状态收敛问题，两者不能互相替代。

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
  "callback": {"endpoint_id": "build-system"},
  "retry_policy": {"before_start": 3, "after_start": 0}
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

## 持久化输出

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

系统还需要：

- 调用方认证、租户隔离和 Job 所有权检查；
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

## 实施顺序

### 第一阶段：单节点持久化服务

- REST 提交、查询、输出、结果和取消接口；
- `JobStore`/`OutputStore` 接口、SQLite/本地文件默认实现、migration、备份与幂等
  admission；
- 声明式 `spec`/`status`、revision 校验、持久事件、Condition 和周期性 full resync；
- 一组 Controller 和一个 Executor，复用现有 `SessionManager`；
- 本地文件 `OutputStore`；
- 事务 Outbox、签名 Webhook、重试和死信；
- 重启、重复提交、重复调谐、漏事件、回调失败、SQLite 磁盘满/损坏和崩溃注入测试。

### 第二阶段：多 Executor 可靠性

- PostgreSQL `JobStore`、S3 兼容 `OutputStore` 和后端一致性测试；
- Executor 注册、能力匹配、lease、heartbeat 和 fencing；
- 处理过期 lease 和不确定 Attempt 的 reconciler；
- 租户配额、公平调度、队列期限和节点 draining；
- failover、网络分区和重复投递一致性测试。

### 第三阶段：可选适配器

- SSE/operator 事件流和语言 SDK；
- 外部 sandbox 与 execution target 适配器；
- 有界 soak 测试和公开可靠性证据；
- 仅当数据库 Outbox 吞吐成为实测瓶颈时增加消息队列适配器。

从提案升级为支持契约之前，每一个状态迁移都必须有故障注入测试，Windows、Linux 和
macOS 必须有真实进程泄漏测试，并公开恢复证据。代码完成不等于可靠性已经得到证明。
