# Tasks

- [x] Task 1: 创建记忆存储抽象层（对象存储接口）
  - [x] 1.1: 创建 `src/agents/memory/storage.py` 定义存储抽象接口
  - [x] 1.2: 实现本地文件存储 `FileMemoryStorage` 
  - [x] 1.3: 添加分布式锁机制处理并发更新
  - [x] 1.4: 创建存储配置（支持切换不同存储后端）

- [x] Task 2: 修改记忆文件路径生成逻辑，支持tenant_id和user_id参数
  - [x] 2.1: 修改 `src/agents/memory/storage.py` 添加按租户/用户生成存储Key的方法

- [x] Task 3: 修改记忆加载逻辑，支持加载和合并租户/用户记忆
  - [x] 3.1: 修改 `src/agents/memory/updater.py` 使用新的存储层
  - [x] 3.2: 添加 `get_memory_data_with_tenant_user` 函数支持tenant/user参数
  - [x] 3.3: 修改 `src/agents/memory/prompt.py` 中的 `_get_memory_context` 支持tenant/user参数

- [x] Task 4: 修改记忆更新逻辑，支持分别更新租户和用户记忆
  - [x] 4.1: 修改 `src/agents/memory/queue.py` 的 ConversationContext 添加 tenant_id 和 user_id
  - [x] 4.2: 修改 `src/agents/memory/updater.py` 的 MemoryUpdater 支持分别更新两种记忆

- [x] Task 5: 修改记忆中间件，支持传递tenant_id和user_id
  - [x] 5.1: 修改 `src/agents/middlewares/memory_middleware.py` 从runtime context获取tenant/user信息

- [x] Task 6: 修改Channel消息处理，支持从Header获取tenant/user信息
  - [x] 6.1: 修改 `src/channels/manager.py` 添加从请求context获取tenant/user的逻辑
  - [x] 6.2: 修改 `src/channels/message_bus.py` 的 InboundMessage 添加 tenant_id 字段

- [x] Task 7: 修改Gateway API，支持从Header传递tenant/user信息
  - [x] 7.1: 检查并修改 `src/gateway/routers/memory.py` 路由处理，缺少Header返回400错误

- [x] Task 8: 修改Client端支持tenant/user参数
  - [x] 8.1: 修改 `src/client.py` 的 chat/stream 方法支持tenant_id和user_id参数
