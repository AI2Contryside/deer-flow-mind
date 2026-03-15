# 基于租户/用户的长期记忆加载与更新 Spec

## Why
当前系统使用全局记忆，所有用户共享同一份长期记忆，无法满足多租户场景下不同租户和用户需要独立记忆的需求。需要实现基于租户ID和用户ID的长期记忆管理，并将记忆存储到对象存储中。

## What Changes
- 在HTTP请求中通过Header（X-Tenant-ID, X-User-ID）获取租户ID和用户ID，缺少则返回异常
- 记忆存储结构：租户级别（共享记忆）+ 用户级别（独立记忆）
- 记忆存储抽象为对象存储接口，支持多种存储实现（本地文件、AWS S3、阿里云OSS等）
- 记忆加载时：根据tenant_id加载租户记忆，根据user_id加载用户记忆，合并后注入system prompt
- 记忆更新时：使用分布式锁处理并发更新问题，分别更新租户级别和用户级别的记忆
- 影响代码位置：Gateway API、Channel消息处理、Memory模块

## Impact
- Affected specs: 长期记忆机制
- Affected code: 
  - `src/gateway/routers/memory.py` - 记忆路由
  - `src/agents/memory/storage.py` - 新增对象存储抽象层
  - `src/agents/memory/updater.py` - 记忆读写
  - `src/agents/memory/queue.py` - 记忆更新队列
  - `src/agents/memory/prompt.py` - 记忆注入
  - `src/agents/middlewares/memory_middleware.py` - 记忆中间件
  - `src/channels/manager.py` - 消息管理器

## ADDED Requirements
### Requirement: HTTP Header获取租户用户ID
系统 SHALL 从HTTP请求Header中解析X-Tenant-ID和X-User-ID

#### Scenario: 正常获取
- **WHEN** HTTP请求包含X-Tenant-ID和X-User-ID Header
- **THEN** 解析出tenant_id和user_id用于后续记忆处理

#### Scenario: 缺少Header（异常情况）
- **WHEN** HTTP请求不包含X-Tenant-ID或X-User-ID Header
- **THEN** 返回400错误，提示缺少必要的租户/用户标识

### Requirement: 记忆存储抽象层
系统 SHALL 提供对象存储抽象接口，支持多种存储实现

#### Scenario: 存储接口
- **WHEN** 需要存储或读取记忆时
- **THEN** 通过抽象的Storage接口进行操作
- **THEN** 支持配置切换不同的存储后端（本地文件、S3、OSS等）

#### Scenario: 并发控制
- **WHEN** 多个请求同时更新同一用户的记忆时
- **THEN** 使用分布式锁机制确保更新顺序
- **THEN** 防止数据覆盖和丢失

### Requirement: 记忆文件存储结构
系统 SHALL 按照租户/用户结构存储记忆文件

#### Scenario: 目录结构（对象存储Key）
- **WHEN** 系统需要存储记忆时
- **THEN** 使用Key `memory/{tenant_id}/shared.json` 作为租户共享记忆
- **THEN** 使用Key `memory/{tenant_id}/{user_id}.json` 作为用户独立记忆

### Requirement: 记忆加载
系统 SHALL 根据tenant_id和user_id加载并合并记忆

#### Scenario: 加载记忆
- **WHEN** 加载记忆用于注入system prompt时
- **THEN** 首先加载租户共享记忆
- **THEN** 然后加载用户独立记忆
- **THEN** 合并两个记忆内容（用户记忆优先级高于租户记忆）

### Requirement: 记忆更新
系统 SHALL 根据tenant_id和user_id分别更新对应记忆

#### Scenario: 更新记忆
- **WHEN** 对话结束后需要更新记忆时
- **THEN** 使用分布式锁锁定对应记忆资源
- **THEN** 先读取当前记忆，合并新内容后更新
- **THEN** 更新租户共享记忆
- **THON** 更新用户独立记忆

## MODIFIED Requirements
### Requirement: 现有全局记忆机制
当前系统使用单一memory.json文件存储全局记忆，需要扩展为多租户多用户结构，并迁移到对象存储
