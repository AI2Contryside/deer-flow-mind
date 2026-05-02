# DeerFlow-Mind 分布式化推进计划

> 状态:草案 v0.1 | 创建日期:2026-04-30
> Owner:待指派 | 协作:trademind-backend / trademind-fe
>
> 本文档是 deer-flow-mind 从「单进程多端口本地服务」演进为「多副本可水平扩展服务」的执行计划。
> 架构讨论与权衡见同目录 `ARCHITECTURE.md`(待补「分布式」章节);本文件只追踪 **任务、负责人、验收标准、进度**。

---

## 0. 目标与非目标

### 目标
- LangGraph Server / Gateway API / Channels 三个进程都能 **N≥2 副本** 部署,任意单实例宕机不影响在途会话。
- 所有持久状态(checkpoint、memory、tenant profile、channels 路由、上传文件、artifacts、配置)**外移到共享存储**(PG / Redis / OSS),worker 进程一律无本地状态。
- Sandbox 由「本地 FS 单例」升级为 **Pod-per-thread**(K8s/Provisioner),支持跨节点漂移。
- 完成多租户(`X-Tenant-ID`)在所有数据面上的隔离与配额。

### 非目标
- 不重写 Agent 业务逻辑(middlewares、subagents 编排语义保持不变)。
- 不替换 LangGraph 框架本身,只切换其运行时(inmem → postgres + redis)。
- 不在本计划内做跨 region 多活;先做单 region 多副本。

---

## 1. 当前架构瓶颈速查

| 子系统 | 现状 | 阻塞点 |
|---|---|---|
| LangGraph Server (:2024) | `langgraph_runtime_inmem`,`max_workers=1`,run 队列 + 状态进程内 | 不可水平扩展;crash 后靠 `mark_orphan_runs_as_interrupted` 兜底 |
| Checkpointer | 已支持 PG(`async_provider.py`) | ✅ 基石就位,需要切到 PG 集群 |
| Sandbox | `LocalSandboxProvider` 单例 + 本地 FS(`backend/.deer-flow/threads/...`) | 跨节点不可见;`AioSandbox` 绑节点 |
| Subagents | `_scheduler_pool` / `_execution_pool` 进程内 ThreadPoolExecutor(各 3 worker) | 父 run 漂移即丢任务 |
| Memory / TenantProfile | `memory.json`、`profile.json`、`usage_log.jsonl` 本地磁盘 + 进程内 debounce 队列 | 多副本会写花 |
| MCP cache | mtime-based 进程内缓存 | 多节点 mtime 不一致,配置漂移 |
| Channels (Feishu/Slack/Telegram) | `MessageBus` 进程内 `asyncio.Queue` + `store.json` 本地路由 | 多副本会重复消费,`chat_id → thread_id` 不一致 |
| Skills | `skills/{public,custom}/` 本地目录 | 上传后只在单节点生效 |
| Gateway API | 大部分 router 无状态;**uploads** 写本地后镜像 OSS | uploads 端点亲和性,需直传 OSS |

---

## 2. 目标拓扑(目标态)

```
                ┌─────────── ALB / Nginx ───────────┐
                │                                    │
         /api/langgraph/*                  /api/*  (其它)
                │                                    │
   ┌────────────▼────────────┐         ┌─────────────▼────────────┐
   │  LangGraph Worker Pool  │         │   Gateway API (无状态 N) │
   │  (N 副本,自托管 runtime)│         │   FastAPI  K8s Deployment│
   └─────┬─────────┬──────────┘         └────────────┬─────────────┘
         │         │                                 │
   ┌─────▼─────┐ ┌─▼──────────────┐         ┌────────▼────────┐
   │ Postgres  │ │ Redis (Stream  │         │ Aliyun OSS      │
   │ Checkpoint│ │  + Pub/Sub +   │         │ uploads/        │
   │ + Memory  │ │  Lock + Cache) │         │ artifacts/      │
   │ + Profile │ └────────────────┘         │ skills/         │
   └───────────┘                            │ memory/profile  │
                                            └─────────────────┘
   ┌──────────────────────────┐
   │ Sandbox Provisioner / K8s│  ← LangGraph workers 通过 sandbox_id attach
   │  (按 thread_id 分配 Pod) │
   └──────────────────────────┘

   ┌──────────────────────────┐
   │ Channels Adapter          │  webhook 类:多副本无状态
   │ (主备 / 多副本)           │  长连接类(Slack Socket Mode):leader election
   └──────────────────────────┘
```

核心原则:**Worker 进程一律无状态;所有「本地磁盘 + 进程内队列」改为「共享存储 + 共享队列」**。

---

## 3. 阶段划分与里程碑

### Phase 1 — 状态外移(预计 2–3 周,业务零感知)
**目标**:把所有「写本地文件 / 进程内队列」的状态外移到 PG/Redis/OSS,Gateway API 先达到多副本可用。

| # | 任务 | 模块 | 验收标准 |
|---|---|---|---|
| 1.1 | Checkpointer 切 PG 集群 + PgBouncer | `src/agents/checkpointer/` | `config.yaml` 切到 `postgres`,跑 30 分钟回归无连接抖动;`application_name` 区分 worker |
| 1.2 | Memory 状态(`memory.json`)迁移到 PG | `src/agents/memory/` | 新增 `memory_facts` / `memory_context` 表;`updater.py` 改 SQL UPSERT;迁移脚本把现网 JSON 导入;读写并发测试通过 |
| 1.3 | TenantProfile(`profile.json` + `usage_log.jsonl` 热数据)迁 PG | `src/agents/tenant_profile/` | `tenant_profile` / `tenant_usage_log` 表;archive 仍走 OSS;bootstrap 路径不变 |
| 1.4 | MemoryQueue 改 Redis Stream + 消费者组 | `src/agents/memory/queue.py` | debounce 改 `ZADD score=ts` + 后台扫描;多副本下不重复处理同一 thread |
| 1.5 | Channels `store.json` → Redis Hash | `src/channels/store.py` | `channel:chat[:topic] → thread_id` 全量读 Redis;新旧双写过渡期 |
| 1.6 | Channels `MessageBus` → Redis Stream | `src/channels/message_bus.py` + `manager.py` | webhook 类 channel 多副本部署不丢不重(同 message_id 幂等去重) |
| 1.7 | Uploads 直传 OSS(STS 临时签名)+ 异步 docling 抽取 | `src/gateway/routers/uploads.py` + 新 worker | 前端走 STS;docling 抽取入独立任务队列;原 `POST /uploads` 仍兼容 |
| 1.8 | Skills 上传落 OSS,worker lazy 同步本地缓存 | `src/skills/` + `src/gateway/routers/skills.py` | `.skill` ZIP 解压目标改 OSS 前缀 `skills/custom/`;worker 启动时拉取 + 监听版本号 |
| 1.9 | MCP / extensions_config.json 配置中心化 | `src/mcp/` + `src/config/` | 至少:配置文件本体放 OSS + 版本号;mtime 缓存替换为 etag/version watch |
| 1.10 | Gateway API 多副本上线 | 部署侧 | K8s `replicas=2`,跑负载测试,P99 无回退;无状态确认 |

**Phase 1 出口准则**
- [ ] Gateway API 双副本生产部署 ≥ 7 天无回滚
- [ ] 所有本地 JSON 文件状态在生产已停写(可保留只读兼容)
- [ ] 监控:PG 检查点写延迟 P99 < 50ms,Redis 队列堆积 < 100

---

### Phase 2 — 计算面分布式(预计 3–5 周)
**目标**:LangGraph Server 与 Sandbox / Subagents 都达到多副本可水平扩展。

| # | 任务 | 模块 | 验收标准 |
|---|---|---|---|
| 2.1 | Sandbox 切 Provisioner/K8s 模式 | `src/sandbox/` + `provisioner` 子服务 | `LocalSandboxProvider` 仅本地开发保留;生产走 Pod-per-thread;`/mnt/user-data/...` 虚拟路径契约不变 |
| 2.2 | Subagents 从线程池切到「LangGraph 子 run」 | `src/subagents/executor.py` | 子任务复用 LangGraph thread + checkpointer;父 run 用 `runs.wait()` 拿结果;原 SSE 事件协议不变 |
| 2.3 | `MAX_CONCURRENT_SUBAGENTS` 改 Redis 全局信号量 | `src/subagents/` + `src/agents/middlewares/` | `SETNX + TTL` 实现租户级配额;`SubagentLimitMiddleware` 改读 Redis 计数 |
| 2.4 | 15 分钟超时改 PG `runs.deadline` + 后台扫描器 | `src/subagents/` + 新 worker | 扫描器幂等,worker crash 后超时仍生效 |
| 2.5 | LangGraph 自托管 runtime 上线(PG + Redis 队列) | 部署侧 + `langgraph.json` | 弃用 `langgraph_runtime_inmem`;worker N 副本;crash 注入测试通过 |
| 2.6 | 重写 orphan 扫描:PG 视角的「running + worker 心跳过期」 | `src/agents/checkpointer/orphan_runs.py` | 不再依赖 `GLOBAL_STORE`;扫描器幂等可多副本;crash recovery 演练通过 |
| 2.7 | 优雅停机(SIGTERM → drain → exit) | 部署侧 + worker 入口 | K8s preStop hook;在途 run 不被强杀;监控 drain 时间 < 60s |

**Phase 2 出口准则**
- [ ] LangGraph worker 在生产环境 N=3 副本运行 ≥ 7 天
- [ ] 主动 kill 一个 worker,在途 run 自动迁移到其它 worker,用户侧无感知
- [ ] Sandbox Pod 在 worker 漂移时正确 attach,无文件丢失

---

### Phase 3 — 弹性、隔离、可观测(持续)
**目标**:多租户配额、可观测性补齐、Channel 主备容灾。

| # | 任务 | 模块 | 验收标准 |
|---|---|---|---|
| 3.1 | Sandbox Pod 按 tenant 配额(CPU/MEM/数量) | `provisioner` + `config.yaml` | `tenant_id` 维度限流;超限返回明确错误 |
| 3.2 | Subagent 全局信号量按 tenant 切片 | `src/subagents/` | 单租户不挤占其它租户配额 |
| 3.3 | Channel 长连接类引入 leader election | `src/channels/` | Redis lock + 心跳;主挂掉 ≤ 30s 内备接管;无重复消费 |
| 3.4 | OpenTelemetry 串 `run_id / thread_id / tenant_id` | `src/logctx/` 扩展 | OTel baggage 全链路;Jaeger/Tempo 可看到完整 trace |
| 3.5 | 指标看板(QPS、PG 写延迟、队列堆积、池占用) | 监控侧 | Grafana Dashboard 上线;告警阈值落库 |
| 3.6 | 配置一致性 CI 巡检 | CI | 所有副本读到同一个 `extensions_config.json` 版本号;漂移即报警 |
| 3.7 | PG/Redis 按租户 sharding(条件触发) | 全栈 | 仅当单实例承压才启用;有迁移预案 |

---

## 4. 风险与回滚策略

| 风险 | 影响面 | 缓解 / 回滚 |
|---|---|---|
| LangGraph 检查点 schema 升级有破坏性 | Phase 1 切 PG 集群可能丢历史会话 | 先做只读演练;迁移脚本可回滚;切换前全量备份 |
| 「按文件 mtime 失效缓存」在多节点失效 | MCP / Skills 配置漂移 | 全部替换为 etag/version watch;Phase 1.9 不延期 |
| Sandbox 虚拟路径契约破坏 | 系统提示词、技能、`present_files` 全坏 | 改底层 provider 时强约束 `/mnt/user-data/...` 不变;加端到端回归 |
| 租户隔离漏洞放大 | Phase 1 后多副本暴露 | `X-Tenant-ID` 传播路径在 Phase 1 就梳理干净;Channels 入口尤其重点 |
| Channel 多副本重复消费 | 用户收到重复回复 | webhook 用 message_id 幂等去重;长连接走 leader election |
| `mark_orphan_runs_as_interrupted` 在分布式下失效 | Run 卡死 | Phase 2.6 重写为 PG 视角,加 worker 心跳表 |

---

## 5. 推进节奏与协作

- **更新机制**:每完成一个任务,负责人在对应行勾 `[x]` 并补「实际完成日期 / PR 链接」。Phase 出口准则全部满足后,在本文件顶部追加 `## Phase N 验收记录` 段落。
- **跨仓影响**:
  - `trademind-backend/`(Go gateway 调用 LangGraph):Phase 2.5 切 runtime 时需要通知,可能要调用方加重试。
  - `trademind-fe/`:Uploads 直传 OSS(任务 1.7)需要前端配合改上传链路;提前对齐。
- **不在本计划内的事**:Agent 业务逻辑改动、新 middleware、新 channel 接入 —— 走各自的迭代,不阻塞分布式化。

---

## 6. 立即可启动的下一步(建议本周内)

1. 拉一次 spike:把 **任务 1.2(Memory → PG)** 和 **任务 1.5(Channels store → Redis)** 在测试环境跑通,验证 schema 与迁移脚本。
2. 同时盘 PG / Redis 资源(规格、网络、备份策略)—— Phase 1 多个任务都依赖。
3. 指定 Phase 1 owner,把上面表格里 owner 列填上。

---

> **维护提示**:本文件随计划演进持续更新;阶段完成后归档到 `docs/archive/` 而不是删除,以保留历史决策上下文。
