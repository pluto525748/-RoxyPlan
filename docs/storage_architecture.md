# RoxyPlan 存储架构

> 当前阶段：V1.5 本地存储封板
> 当前实现：Local JSON
> 未实现：数据库、服务器、数据上传和跨设备同步

## 1. 设计目标

业务 Manager 负责规则与状态变化，Repository 只负责持久化。桌面端现有业务接口、JSON 文件位置和私人数据策略保持不变，未来数据库适配器可以在不重写业务规则的前提下接入。

```text
PySide6 UI / Agent Tools
          |
       Manager
          |
   Repository interface
          |
 Local JSON adapter (current)
 Database adapter (future, not implemented)
```

## 2. Manager 职责

| Manager | 负责 | 不负责 |
| --- | --- | --- |
| `GrowthManager` | 计划、完成、行动记录、复盘规则和日期隔离 | JSON 编码、文件替换 |
| `MemoryManager` | 分类、去重、冲突、归档、迁移判断 | 文件读写和备份文件创建 |
| `MemoryCandidateManager` | 候选去重、确认、拒绝和状态变化 | 候选文件写入 |
| `ChatHistoryManager` | 会话、消息、标题、摘要和裁剪规则 | 历史与摘要文件写入 |

Manager 维护运行时数据缓存。每次确定性写操作前会从 Repository 刷新最新快照，再把变更后的完整领域文档交给 Repository 保存。

## 3. Repository 接口

`modules/repositories/` 包含：

- `GrowthRepository`：读取/保存计划、行动记录和成长日志。
- `MemoryRepository`：读取/保存长期记忆、候选、冲突并创建迁移备份。
- `ChatRepository`：读取/保存会话、消息和摘要。
- `LocalJsonGrowthRepository`、`LocalJsonMemoryRepository`、`LocalJsonChatRepository`：当前本地实现。

Repository 不做计划完成、记忆分类、候选确认或摘要生成等业务判断。

## 4. 当前 Local JSON 行为

- 私人成长、候选、冲突和聊天数据继续放在 `data/private/`。
- 长期记忆继续使用 Git 忽略的根目录 `memory.json`。
- JSON 继续使用 UTF-8、`ensure_ascii=False` 和缩进格式。
- 写入使用同目录唯一临时文件、`flush`、`fsync` 和原子替换，避免半写入文件。
- 文件缺失时由 Manager 与 Repository 协作创建默认结构。
- 旧成长文件可安全导入新私有目录，旧文件不会自动删除。
- 旧长期记忆迁移前由 Repository 创建逐字节备份；备份失败则不覆盖旧文件。
- 读取正常旧文件时，不会仅为补充新 `uid` 强制重写私人内容。

### 原子写入与并发边界

同一数据文件的读改写通过 `data/private/locks/` 中的轻量文件锁协调。桌面端和 Local Web 使用独立 Manager 时，写操作会在持锁后重新加载最新快照，因此常见的并发新增不会互相覆盖。

当前仍是单机原型：多个 JSON 文件之间没有数据库事务，网络共享目录和多台电脑并发写入不受支持。强制结束进程时，跨文件组合操作可能只完成其中一部分，但单个 JSON 文件应保持旧版或新版完整状态。

成长、会话和记忆相关 JSON 读取失败时，会在 `data/private/backups/` 保存带时间戳的损坏副本。原损坏文件不会在读取阶段被静默覆盖。

## 5. ID 与界面序号

新任务、行动、复盘、记忆、候选和冲突会增加稳定字符串 `uid`。原整数 `id` 继续保留，用于“完成计划1”等命令和界面列表序号。

- `uid`：内部持久标识，未来适合映射云端 UUID。
- `id`：当前本地显示与兼容序号，不作为未来跨设备主键。
- 会话与消息原本已经使用稳定字符串 ID。

旧记录没有 `uid` 时仍可读取和操作，不会被强制批量迁移。

## 6. 未来数据库适配原则

未来适配器应实现现有 Repository 接口，不应把 SQL、HTTP 或数据库对象泄漏给 Manager。

正式接入前仍需补齐：

1. 用户与设备身份边界。
2. 服务端认证和逐用户授权。
3. 写操作幂等键与乐观并发版本。
4. 离线队列和冲突处理规则。
5. 敏感记忆、聊天和知识文件的加密、导出与删除策略。
6. Local JSON 到数据库的可回退迁移工具。

本项目当前没有数据库适配器，也不会自动上传任何本地文件。
