# Checklist

- [x] 存储接口抽象正确：验证Storage接口定义满足读写锁需求
- [x] 本地文件存储实现正确：验证FileMemoryStorage能正常读写
- [x] 分布式锁机制正确：验证并发更新时能正确加锁和释放
- [x] 记忆Key生成正确：验证 `memory/{tenant_id}/shared.json` 和 `memory/{tenant_id}/{user_id}.json` 生成
- [x] 租户记忆加载正确：验证加载租户共享记忆返回正确内容
- [x] 用户记忆加载正确：验证加载用户独立记忆返回正确内容
- [x] 记忆合并正确：验证租户记忆和用户记忆合并后优先级正确
- [x] 记忆更新正确：验证分别更新租户和用户记忆文件
- [x] Header解析正确：验证从X-Tenant-ID和X-User-ID Header正确解析
- [x] 缺少Header处理正确：验证缺少Header时返回400错误
- [x] Client参数传递正确：验证DeerFlowClient支持tenant_id和user_id参数
