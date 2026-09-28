# RoxyPlan 长期记忆系统设计

> 本文是长期结构设计草案，不是当前运行时能力清单。V2.2 的实际写入、确认、敏感信息和候选落盘契约以 [memory_system.md](memory_system.md) 与 [MEMORY.md](MEMORY.md) 为准；若本文的未来设置或流程示例与当前契约不同，不得据此扩大生产行为。

## 1. 设计目标

长期记忆系统用于帮助 Roxy 跨会话理解用户。

它不是聊天记录的简单堆叠，而是对用户长期稳定信息的结构化保存。

长期记忆应服务于：

- 更自然的陪伴。
- 更准确的学习和项目支持。
- 更持续的成长体验。
- 更可控的个性化互动。

核心原则：

- 用户可查看。
- 用户可编辑。
- 用户可删除。
- 敏感信息谨慎保存。
- 记忆来源可追溯。
- 普通聊天记录和长期记忆分离。
- 知识库内容和长期记忆分离。

## 2. 记忆分类

RoxyPlan 的长期记忆分为七类：

1. 用户档案
2. 长期目标
3. 学习记录
4. 项目记录
5. 健康记录
6. 成长记录
7. 兴趣爱好

每类记忆的用途不同，敏感程度也不同。系统不应把所有信息都放进同一个无结构列表。

## 3. 本地长期记忆文件 总体结构

本地长期记忆文件 建议采用以下顶层结构：

```json
{
  "version": 1,
  "updated_at": "",
  "user_profile": {},
  "long_term_goals": [],
  "learning_records": [],
  "project_records": [],
  "health_records": [],
  "growth_records": {},
  "interests": [],
  "memory_settings": {},
  "audit_log": []
}
```

说明：

- `version`：记忆结构版本，用于未来升级。
- `updated_at`：最后更新时间。
- `user_profile`：用户档案。
- `long_term_goals`：长期目标。
- `learning_records`：学习记录。
- `project_records`：项目记录。
- `health_records`：健康记录。
- `growth_records`：成长记录。
- `interests`：兴趣爱好。
- `memory_settings`：记忆系统设置。
- `audit_log`：记忆变更记录。

## 4. 用户档案

### 4.1 用途

用户档案用于保存稳定、基础、低频变化的信息。

它帮助 Roxy 了解用户希望如何被称呼、偏好的交流方式、语言习惯和基础边界。

### 4.2 结构示例

```json
{
  "user_profile": {
    "display_name": "",
    "preferred_name": "",
    "pronouns": "",
    "language_preference": "zh-CN",
    "timezone": "",
    "communication_style": "",
    "preferred_tone": "",
    "boundaries": [],
    "notes": [],
    "updated_at": ""
  }
}
```

### 4.3 字段说明

- `display_name`：用户显示名称。
- `preferred_name`：用户希望 Roxy 使用的称呼。
- `pronouns`：用户代词或称谓偏好，可为空。
- `language_preference`：默认交流语言。
- `timezone`：用户所在时区，用于提醒和计划。
- `communication_style`：用户偏好的沟通方式，例如简洁、详细、温柔、直接。
- `preferred_tone`：用户偏好的 Roxy 语气。
- `boundaries`：用户明确提出的互动边界。
- `notes`：其他稳定档案备注。
- `updated_at`：用户档案最后更新时间。

## 5. 长期目标

### 5.1 用途

长期目标用于记录用户希望持续推进的重要方向。

例如：

- 学习某门课程。
- 完成一个项目。
- 改善作息。
- 写一本小说。
- 准备考试。
- 做一个产品。

### 5.2 结构示例

```json
{
  "long_term_goals": [
    {
      "goal_id": "",
      "title": "",
      "description": "",
      "category": "",
      "status": "active",
      "priority": "medium",
      "start_date": "",
      "target_date": "",
      "milestones": [],
      "progress_notes": [],
      "related_projects": [],
      "related_learning_records": [],
      "created_at": "",
      "updated_at": ""
    }
  ]
}
```

### 5.3 字段说明

- `goal_id`：长期目标唯一 ID。
- `title`：目标标题。
- `description`：目标描述。
- `category`：目标分类，例如学习、健康、项目、创作、生活。
- `status`：目标状态，例如 active、paused、completed、cancelled。
- `priority`：优先级，例如 low、medium、high。
- `start_date`：开始日期。
- `target_date`：目标完成日期。
- `milestones`：阶段性里程碑。
- `progress_notes`：进度记录。
- `related_projects`：关联项目 ID。
- `related_learning_records`：关联学习记录 ID。
- `created_at`：创建时间。
- `updated_at`：更新时间。

## 6. 学习记录

### 6.1 用途

学习记录用于保存用户的学习方向、课程、进度、薄弱点和复习需求。

它帮助 Roxy 做学习陪伴、复习提醒、知识回顾和计划调整。

### 6.2 结构示例

```json
{
  "learning_records": [
    {
      "learning_id": "",
      "subject": "",
      "topic": "",
      "source": "",
      "status": "learning",
      "progress": 0,
      "strengths": [],
      "weak_points": [],
      "review_schedule": [],
      "notes": [],
      "related_goals": [],
      "related_knowledge_files": [],
      "created_at": "",
      "updated_at": ""
    }
  ]
}
```

### 6.3 字段说明

- `learning_id`：学习记录唯一 ID。
- `subject`：学科或学习领域。
- `topic`：具体主题。
- `source`：学习来源，例如课程、书籍、文档、视频。
- `status`：学习状态，例如 planned、learning、reviewing、completed、paused。
- `progress`：学习进度，建议使用 0 到 100。
- `strengths`：用户已掌握较好的部分。
- `weak_points`：薄弱点。
- `review_schedule`：复习计划。
- `notes`：学习备注。
- `related_goals`：关联长期目标 ID。
- `related_knowledge_files`：关联知识投喂文件 ID。
- `created_at`：创建时间。
- `updated_at`：更新时间。

## 7. 项目记录

### 7.1 用途

项目记录用于保存用户正在推进的项目背景、状态、任务、决策和上下文。

项目可以是：

- 软件项目。
- 写作项目。
- 学习项目。
- 产品设计。
- 游戏设计。
- 研究项目。
- 个人计划。

### 7.2 结构示例

```json
{
  "project_records": [
    {
      "project_id": "",
      "name": "",
      "description": "",
      "type": "",
      "status": "active",
      "priority": "medium",
      "current_focus": "",
      "decisions": [],
      "tasks": [],
      "risks": [],
      "related_goals": [],
      "related_knowledge_files": [],
      "created_at": "",
      "updated_at": ""
    }
  ]
}
```

### 7.3 字段说明

- `project_id`：项目唯一 ID。
- `name`：项目名称。
- `description`：项目描述。
- `type`：项目类型，例如 software、writing、study、product、game、research。
- `status`：项目状态，例如 active、paused、completed、archived。
- `priority`：优先级。
- `current_focus`：当前关注点。
- `decisions`：重要决策记录。
- `tasks`：任务列表或任务摘要。
- `risks`：风险、阻塞点或注意事项。
- `related_goals`：关联长期目标 ID。
- `related_knowledge_files`：关联知识投喂文件 ID。
- `created_at`：创建时间。
- `updated_at`：更新时间。

## 8. 健康记录

### 8.1 用途

健康记录用于保存用户允许记录的轻量健康习惯和提醒偏好。

它不用于医疗诊断，也不应保存未经用户明确确认的敏感健康信息。

适合记录：

- 作息偏好。
- 喝水提醒偏好。
- 久坐提醒偏好。
- 用眼休息偏好。
- 用户明确允许记录的健康目标。

### 8.2 结构示例

```json
{
  "health_records": [
    {
      "health_id": "",
      "type": "",
      "title": "",
      "description": "",
      "sensitivity": "normal",
      "reminder_enabled": false,
      "reminder_rule": "",
      "status": "active",
      "notes": [],
      "created_at": "",
      "updated_at": ""
    }
  ]
}
```

### 8.3 字段说明

- `health_id`：健康记录唯一 ID。
- `type`：记录类型，例如 sleep、water、break、exercise、eye_rest、stress。
- `title`：健康记录标题。
- `description`：说明。
- `sensitivity`：敏感等级，例如 normal、sensitive。
- `reminder_enabled`：是否开启提醒。
- `reminder_rule`：提醒规则描述。
- `status`：状态，例如 active、paused、archived。
- `notes`：用户确认过的备注。
- `created_at`：创建时间。
- `updated_at`：更新时间。

### 8.4 健康记忆边界

健康记录必须谨慎。

规则：

- 不做医疗诊断。
- 不自动保存疾病信息。
- 不保存药物、病史等敏感信息，除非用户明确要求。
- 不用健康记录责备用户。
- 严重症状应建议用户咨询专业医生。

## 9. 成长记录

### 9.1 用途

成长记录用于保存 Roxy 与用户关系、互动、能力和陪伴阶段的长期变化。

它不是为了强迫用户打卡，而是为了体现长期陪伴感。

### 9.2 结构示例

```json
{
  "growth_records": {
    "level": 1,
    "stage": "initial",
    "relationship_phase": "first_meeting",
    "companionship_days": 0,
    "conversation_count": 0,
    "confirmed_memory_count": 0,
    "knowledge_file_count": 0,
    "unlocked_traits": [],
    "milestones": [],
    "last_interaction_at": "",
    "updated_at": ""
  }
}
```

### 9.3 字段说明

- `level`：成长等级。
- `stage`：成长阶段，例如 initial、familiar、trusted、partner。
- `relationship_phase`：关系阶段，例如 first_meeting、getting_familiar、mutual_understanding、long_term_partner。
- `companionship_days`：陪伴天数。
- `conversation_count`：累计对话次数。
- `confirmed_memory_count`：用户确认的长期记忆数量。
- `knowledge_file_count`：已投喂知识文件数量。
- `unlocked_traits`：已解锁的角色特质或互动能力。
- `milestones`：成长里程碑。
- `last_interaction_at`：最近互动时间。
- `updated_at`：更新时间。

## 10. 兴趣爱好

### 10.1 用途

兴趣爱好用于记录用户稳定的喜好、创作方向、娱乐偏好和关注主题。

它帮助 Roxy 更自然地提供建议、聊天和知识关联。

### 10.2 结构示例

```json
{
  "interests": [
    {
      "interest_id": "",
      "name": "",
      "category": "",
      "description": "",
      "preference_level": "medium",
      "related_projects": [],
      "related_goals": [],
      "notes": [],
      "created_at": "",
      "updated_at": ""
    }
  ]
}
```

### 10.3 字段说明

- `interest_id`：兴趣唯一 ID。
- `name`：兴趣名称。
- `category`：兴趣分类，例如 game、anime、writing、music、programming、reading。
- `description`：兴趣描述。
- `preference_level`：偏好程度，例如 low、medium、high。
- `related_projects`：关联项目 ID。
- `related_goals`：关联长期目标 ID。
- `notes`：备注。
- `created_at`：创建时间。
- `updated_at`：更新时间。

## 11. 记忆系统设置

### 11.1 用途

记忆系统设置用于控制 Roxy 如何保存、使用和询问记忆。

### 11.2 结构示例

```json
{
  "memory_settings": {
    "auto_memory_enabled": false,
    "confirm_before_saving": true,
    "sensitive_memory_requires_confirmation": true,
    "allow_health_memory": false,
    "allow_growth_tracking": true,
    "memory_review_interval": "monthly",
    "last_reviewed_at": ""
  }
}
```

### 11.3 字段说明

- `auto_memory_enabled`：是否允许自动建议记忆。
- `confirm_before_saving`：保存前是否需要用户确认。
- `sensitive_memory_requires_confirmation`：敏感记忆是否必须确认。
- `allow_health_memory`：是否允许保存健康相关记忆。
- `allow_growth_tracking`：是否允许成长记录。
- `memory_review_interval`：记忆回顾周期。
- `last_reviewed_at`：最近一次记忆回顾时间。

## 12. 记忆审计日志

### 12.1 用途

审计日志用于记录记忆变化，让用户知道 Roxy 何时创建、修改或删除了哪些记忆。

### 12.2 结构示例

```json
{
  "audit_log": [
    {
      "event_id": "",
      "action": "",
      "memory_type": "",
      "memory_id": "",
      "summary": "",
      "source": "",
      "created_at": ""
    }
  ]
}
```

### 12.3 字段说明

- `event_id`：事件唯一 ID。
- `action`：操作类型，例如 create、update、delete、review。
- `memory_type`：记忆类型，例如 user_profile、long_term_goals、learning_records。
- `memory_id`：被操作的记忆 ID。
- `summary`：变更摘要。
- `source`：来源，例如 user_confirmed、manual_edit、review_update。
- `created_at`：事件时间。

## 13. 完整 本地长期记忆文件 示例结构

```json
{
  "version": 1,
  "updated_at": "",
  "user_profile": {
    "display_name": "",
    "preferred_name": "",
    "pronouns": "",
    "language_preference": "zh-CN",
    "timezone": "",
    "communication_style": "",
    "preferred_tone": "",
    "boundaries": [],
    "notes": [],
    "updated_at": ""
  },
  "long_term_goals": [],
  "learning_records": [],
  "project_records": [],
  "health_records": [],
  "growth_records": {
    "level": 1,
    "stage": "initial",
    "relationship_phase": "first_meeting",
    "companionship_days": 0,
    "conversation_count": 0,
    "confirmed_memory_count": 0,
    "knowledge_file_count": 0,
    "unlocked_traits": [],
    "milestones": [],
    "last_interaction_at": "",
    "updated_at": ""
  },
  "interests": [],
  "memory_settings": {
    "auto_memory_enabled": false,
    "confirm_before_saving": true,
    "sensitive_memory_requires_confirmation": true,
    "allow_health_memory": false,
    "allow_growth_tracking": true,
    "memory_review_interval": "monthly",
    "last_reviewed_at": ""
  },
  "audit_log": []
}
```

## 14. 使用规则

### 14.1 写入规则

只有以下情况可以写入长期记忆：

- 用户明确要求记住。
- Roxy 建议保存后，用户确认。
- 用户在记忆管理界面手动编辑。

### 14.2 更新规则

当用户提供新信息与旧记忆冲突时，应：

1. 提醒用户存在旧记忆。
2. 询问是否更新。
3. 更新后记录审计日志。

### 14.3 删除规则

用户可以删除：

- 单条记忆。
- 某一类记忆。
- 全部长期记忆。

删除后不应在后续对话中继续使用该记忆。

### 14.4 敏感信息规则

健康、身份、家庭、财务、联系方式、账号安全相关信息都应被视为敏感信息。

敏感信息默认不保存，除非用户明确要求并确认。

## 总结

RoxyPlan 的长期记忆系统应采用结构化、可控、可审计的设计。

七类记忆分别承担不同职责：

- 用户档案提供基础个性化。
- 长期目标提供持续陪伴方向。
- 学习记录支持学习监督。
- 项目记录支持长期项目上下文。
- 健康记录只做轻量提醒且必须谨慎。
- 成长记录体现 Roxy 与用户的长期关系。
- 兴趣爱好让日常互动更自然。

记忆系统的核心不是“记得越多越好”，而是“记得重要、准确、用户允许的内容”。
