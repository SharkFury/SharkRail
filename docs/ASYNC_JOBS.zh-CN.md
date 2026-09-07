# 可靠异步任务架构

状态：设计提案；SharkRail v0.1 尚未实现。

本方案在现有运行时之上增加一个可选、自托管的 C/S 控制面。客户端提交命令并得到
持久化 `job_id` 后即可断开；SharkRail 负责执行监督、输出保留、取消、结果存储，并在
任务结束后可靠通知客户端。

它不是工作流引擎或商业托管服务。一个 Job 只监督一个命令或交互会话；DAG、定时
调度、业务重试、凭据系统和沙箱供应仍属于上层系统或执行目标。

英文规范见 [ASYNC_JOBS.md](ASYNC_JOBS.md)。

## 目标与承诺

- 只有任务持久化成功后才返回已接受；
- 相同幂等键的重复请求不会创建重复任务；
- 同一 Job 最多只有一个当前执行尝试能够更新结果；
- 每个已启动 Attempt 都进入明确终态或 `executor_lost`；
- 终态结果与通知意图在同一个事务中提交；
- Webhook 至少投递一次，接收方可以可靠去重；
- Client、API、Scheduler 和通知进程重启不会丢失已接受任务；
- 输出丢失、Executor 丢失、重试和回调失败都必须显式可见。

系统不能承诺命令严格 Exactly Once。主机可能在启动进程后、持久化启动确认前宕机，
此时无法证明带外部副作用的命令是否已经执行。因此默认只允许启动前重试；启动后不
自动重试，除非调用方明确声明命令幂等。

## 总体架构

```text
Client
  |
  | HTTP：提交、查询、取消
  v
API Server ------------------------------+
  |                                      |
  | 数据库事务                            | 查询结果
  v                                      |
PostgreSQL                               |
  | Job · Attempt · Lease · Outbox       |
  |                                      |
  +--> Scheduler --> Executor Node ------+
  |                    |
  |                    +--> SessionManager --> OS Process / PTY
  |                    +--> OutputSink --> 文件或对象存储
  |
  +--> Notification Dispatcher --> 签名 Webhook --> Client
```

`SessionManager` 继续作为进程生命周期的语义核心；新增 `JobManager` 只负责持久化、
调度、恢复和通知，不复制进程监督逻辑。生产环境建议使用 PostgreSQL 和 S3 兼容对象
存储；SQLite 与本地文件仅用于开发和单机验证，不能宣称等价的多节点保证。

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

终态不可修改。回调投递状态必须独立保存，通知失败不能把已经成功的命令改成失败。
每次状态更新增加 `revision`；事件使用唯一 `event_id` 和 Job 内单调递增序号。

## Lease、Heartbeat 与 Fencing

Scheduler 在一个事务中领取任务，并记录：

```text
executor_id
lease_expires_at
lease_epoch
```

Executor 必须在 lease 过期前发送 heartbeat。每次重新分配都增加 `lease_epoch`，所有
Executor 写入必须同时校验 epoch 和 Job revision。旧 Executor 即使恢复，也不能覆盖
当前所有者的结果。数据库必须约束同一 Job 最多只有一个 active Attempt。

队列需要全局、租户、Executor 和策略容量限制。过载时返回 `429` 或 `503`，不能无限
积压。超过最大排队时间的任务进入 `expired` 并产生终态通知。

## 故障与重启语义

| 故障 | 必须实现的行为 |
| --- | --- |
| Client 断线 | 不影响已经接受的 Job |
| API 重启 | 从 PostgreSQL 恢复，不丢任务 |
| Scheduler 重启 | 对过期 lease 执行 reconciliation |
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

长任务输出不能只保存在 `SessionManager` 内存中。新增输出接口：

```python
class OutputSink(Protocol):
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

- `jobs`：不可变请求、请求摘要、业务状态、revision 和策略；
- `job_attempts`：Executor、lease epoch、时间和结果；
- `executor_nodes`：容量、能力、heartbeat 和 drain 状态；
- `output_objects`：字节区间、hash、保留策略和 storage key；
- `callback_endpoints`：租户目标地址和密钥引用；
- `callback_outbox`：不可变通知事件和投递状态；
- `callback_deliveries`：投递历史、响应类别和下次重试时间；
- `audit_events`：有界的管理与安全审计。

数据库必须强制幂等键唯一、event ID 唯一、每个 Job 一个 active Attempt、合法状态迁移、
revision 校验和 fencing epoch 校验，不能只依赖应用层判断。

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
- PostgreSQL `JobStore` 与幂等 admission；
- 一个 Scheduler 和 Executor，复用现有 `SessionManager`；
- 本地文件 `OutputSink`；
- 事务 Outbox、签名 Webhook、重试和死信；
- 重启、重复提交、回调失败和崩溃注入测试。

### 第二阶段：多 Executor 可靠性

- Executor 注册、能力匹配、lease、heartbeat 和 fencing；
- 处理过期 lease 和不确定 Attempt 的 reconciler；
- S3 兼容输出存储；
- 租户配额、公平调度、队列期限和节点 draining；
- failover、网络分区和重复投递一致性测试。

### 第三阶段：可选适配器

- SSE/operator 事件流和语言 SDK；
- 外部 sandbox 与 execution target 适配器；
- 有界 soak 测试和公开可靠性证据；
- 仅当 PostgreSQL Outbox 吞吐成为实测瓶颈时增加消息队列适配器。

从提案升级为支持契约之前，每一个状态迁移都必须有故障注入测试，Windows、Linux 和
macOS 必须有真实进程泄漏测试，并公开恢复证据。代码完成不等于可靠性已经得到证明。
