# RoxyPlan 文档

本目录记录 RoxyPlan 的产品设计、架构边界、阶段路线和后续能力规划。

## 当前阶段

项目当前处于 **V0.9 桌宠交互与轻量成长闭环原型阶段**。

如果只想快速了解“现在做到什么程度”，优先阅读 `current_status.md`。

已实现的原型能力包括：

- PySide6 桌面角色与轻量聊天窗口
- 透明背景图片加载和桌面常驻
- 随机气泡提示与 88 条本地提示语
- `jump`、`nod`、`thinking / shake`、`study / scale`、`sleep / wake` 状态动作
- 本地人格数据与基础长期记忆读写
- `memory.example.json` 示例记忆文件与本地私有 `memory.json`
- `data/knowledge/` 读取 `.txt` / `.md` 文件正文
- 轻量关键词匹配，将相关知识片段加入聊天 prompt
- 设置面板入口和多帧舞蹈素材播放框架
- 今日计划、任务完成记录和简单复盘聊天命令
- Ollama / Qwen 本地模型调用尝试
- QThread / Worker 异步回复
- 基础测试脚本和 Windows 启动脚本

## 文档索引

- `current_status.md`：当前完成度、已实现能力、原型边界和下一步建议
- `product_design.md`：产品定位和完整体验设想
- `roxy_personality.md`：Roxy 人格、表达和安全边界
- `pet_interaction.md`：桌宠状态和交互设计
- `ROADMAP.md`：当前阶段与后续路线
- `ARCHITECTURE.md`：现有模块边界和数据流
- `MEMORY.md`：长期记忆原型说明
- `knowledge_feed_design.md`：知识文件解析、检索和引用规划
- `growth_system.md`：成长记录、周报和月报规划
- `memory_design.md`：长期记忆分类和字段设计
- `voice_system.md`：语音能力规划
- `mobile_design.md`：移动端轻量访问规划
- `data_architecture.md`：长期数据架构设想
- `mvp_plan.md`、`product_roadmap.md`：阶段范围和版本规划

## 阅读说明

- 标记为“已实现”的内容对应当前 V0.9 原型。
- `current_status.md` 和本文件优先反映当前代码状态。
- 设计文档中的数据库、语音、向量知识库、移动端和复杂成长系统仍属于计划中能力。
- 文档描述的是原型演进方向，不代表完整商业产品承诺。
