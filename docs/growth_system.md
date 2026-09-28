# 成长日志系统设计

> 当前状态（V2.2 封版候选）：已实现“今日计划 -> 行动记录 -> 今日复盘 -> 按天成长日志 -> 自然月查看/统计”的本地闭环。轻量自然月总结已经可按需生成；下文的完整五维记录、长期趋势、周报和完整月报仍属于未来规划。复杂计划修改、合并、改期和重排不是当前模型主入口保证能力。

## V0.9 已实现范围

- 今日计划：核心保证添加、查看和完成，按日期保存并保留跨天历史；删除及复杂修改只保留受限兼容入口。
- 行动记录：通过聊天或成长面板记录当天推进内容。
- 今日复盘：统计计划总数、完成数、未完成数，并汇总当天行动；计划之外完成的事情标为“计划外行动”。
- 成长日志：每天最多保存一条复盘；启动自动补齐不会覆盖已有条目，用户明确要求重新生成指定日期时才允许修订。
- 成长面板：提供计划、行动、复盘、自然月统计和每天成长记录等轻量区域。
- 统一管理：`modules/growth_manager.py` 负责计划、行动、规则复盘和成长日志；`modules/review_backfill.py` 只负责启动时昨日缺失日志的本地补齐。
- 本地隐私：数据保存在 `data/private/today_plan.json`、`action_log.json`、`growth_log.json`，整个私有目录均不提交公开仓库。
- 兼容迁移：首次升级会把旧 `data/*.json` 成长数据转换到私有目录，同时保留旧文件。

V0.9 使用轻量 JSON 文件验证交互闭环，没有引入数据库、向量检索或新的模型依赖。下文描述的是长期设计方向，不代表当前已经全部实现。

2026-09-16 交互收口不增加成长功能：模型可针对具体真实进展提出 `add_action_log` 建议，但必须是 `possible_action + explicit_command=false + needs_confirmation=true`，由共享流程建立 `action_log_offer` 后才消费用户确认。普通模型回复不能独自承诺写入；没有成功 ToolResult 时，“我把它记进今天的行动记录”等声明会被最终守卫拦截。纯文学叙述中的记录动作不属于应用写入声明。用户暂缓的两种自然月总结表达本轮未修改。

## V2.2 封版收口已实现范围

- 全局设置“启动时自动补全昨日成长复盘”对应 `auto_complete_yesterday_review`，默认开启，并与“晚间复盘提醒”相互独立。
- 每次桌面程序启动只检查昨天；不追补更早日期。昨天没有计划和行动时不创建空日志，已有昨天日志时完全不变。
- 缺失日志使用本地已核验数据生成完整快照，包含计划稳定 ID、标题、完成/未完成状态、时段、时长和行动记录，不调用大模型。
- 用户明确要求“重新生成昨天的复盘”时可以手动修订；后台自动补齐始终保持原子、幂等。
- 明确要求“生成/保存今天复盘”时从当前最新计划和行动保存或修订当天稳定条目；“看看今天完成了什么”等反思性询问只生成只读预览，不提前锁定或写入成长日志。
- 普通首次失败只写脱敏 diagnostics；连续失败或数据损坏才提醒用户，且不阻断桌宠启动。损坏数据不会被空结构覆盖。
- 成长日志查询和面板统计按 `YYYY-MM` 自然月隔离；模型只可基于成功工具结果中的已核验数据生成自然总结，调用失败时返回确定性统计。

## V1.0 已实现范围

- 新增 `modules/intent_router.py`，只负责规则识别和结构化意图结果，不直接读写文件或修改 UI。
- 支持自然表达添加、查看和完成计划，添加行动记录、生成/保存复盘及查看成长数据。删除、修改、合并、改期与重排不是模型主入口保证能力；仅在已有确定性兼容路径可靠时尝试，否则明确说明本次未完成并给出可执行说法。
- 支持明确的长期记忆请求；计划、行动和复盘不会自动写入 `memory.json`。
- 固定命令保持最高优先级，原有命令处理逻辑不变。
- 完成计划时先使用包含关系，再使用 `difflib.SequenceMatcher` 做轻量相似度匹配。
- 唯一高相似结果可自动完成；多个相近结果只展示候选并等待用户确认。
- 未命中成长意图的输入继续进入普通聊天，不增加新的模型依赖。
- 新增 `modules/proactive_manager.py`，只读取 `GrowthManager` 状态并返回提醒类型与文本，不修改计划或调用模型。
- 主动提醒覆盖未完成计划、无行动记录、晚间复盘、完成任务鼓励和长时间未互动。
- 同类型和整体提醒均有运行时冷却；用户发送消息后的两分钟内不主动提醒。
- 晚间复盘提醒每天最多一次，dance 和繁忙动作期间不会插入提醒。
- 设置面板提供主动陪伴开关、提醒间隔、晚间复盘提醒、未互动提醒以及独立的昨日复盘自动补全开关。
- “先别提醒我”与“恢复提醒”可在本次运行中暂停或恢复主动陪伴。

V1.0 的自然语言识别和主动提醒都是本地规则原型，不等同于通用语义理解或复杂日程系统。规则会优先保证数据操作谨慎，无法确定时不自动修改计划或长期记忆。

## V1.6 生命周期规则

- 计划状态为 `pending / completed / cancelled`，取消保留记录，删除需要二次确认且不可从当前计划恢复。
- 底层兼容能力仍可修改标题、日期、时间段、时长、优先级和备注，已完成计划修改核心字段前需先重新打开；这些复杂能力默认不向模型开放。
- 相似计划可在受支持的本地确定性路径中提示更新原计划、明确保留两条或取消，不静默复制；无法可靠处理时诚实退出。
- 完成计划不会自动创建行动记录；只有用户明确记录或组合请求时才写入，完全相同的当天行动不会重复保存。
- 用户明确生成或保存今日复盘时始终从最新计划和行动重新生成并更新当天条目，不会锁住当天后续操作；普通查看今日状态不保存。
- 同一天成长日志保存到同一稳定条目；用户明确重新生成时更新 `updated_at` 并增加 `revision`。启动自动补齐发现已有日志时完全不写入，历史日期不会因今天保存而改变。

## 1. 设计目标

成长日志系统用于记录用户从 22 岁到毕业期间的成长过程。

它不是单纯的打卡系统，而是一个长期人生阶段记录系统。Roxy 通过学习、项目、健身、收入和心情五类记录，帮助用户看见自己的变化、积累和方向。

核心目标：

- 记录长期成长轨迹。
- 帮助用户复盘每周、每月的进展。
- 发现学习、项目、健康、收入和心情之间的关联。
- 降低自我管理压力，让成长可见但不压迫。
- 为 Roxy 的长期陪伴和成长系统提供上下文。

时间范围：

- 起点：用户 22 岁。
- 终点：毕业。
- 可扩展：毕业后可切换为新的成长阶段，例如工作初期、创业期、研究生阶段等。

## 2. 记录内容

成长日志系统包含五类核心记录：

1. 学习记录
2. 项目进度
3. 健身记录
4. 收入记录
5. 心情记录

这五类记录应独立保存，但在周报和月报中可以被综合分析。

## 3. 数据结构

### 3.1 顶层结构

成长日志建议采用以下顶层结构：

```json
{
  "growth_journal": {
    "version": 1,
    "stage": {},
    "learning_records": [],
    "project_progress": [],
    "fitness_records": [],
    "income_records": [],
    "mood_records": [],
    "weekly_reports": [],
    "monthly_reports": [],
    "settings": {}
  }
}
```

字段说明：

- `version`：成长日志结构版本。
- `stage`：当前人生阶段信息。
- `learning_records`：学习记录。
- `project_progress`：项目进度记录。
- `fitness_records`：健身记录。
- `income_records`：收入记录。
- `mood_records`：心情记录。
- `weekly_reports`：周报记录。
- `monthly_reports`：月报记录。
- `settings`：成长日志系统设置。

### 3.2 阶段信息

```json
{
  "stage": {
    "stage_id": "",
    "name": "22_to_graduation",
    "display_name": "22岁到毕业",
    "start_age": 22,
    "start_date": "",
    "expected_graduation_date": "",
    "current_status": "active",
    "main_theme": "",
    "long_term_keywords": [],
    "created_at": "",
    "updated_at": ""
  }
}
```

字段说明：

- `stage_id`：阶段唯一 ID。
- `name`：阶段内部名称。
- `display_name`：阶段展示名称。
- `start_age`：开始年龄。
- `start_date`：阶段开始日期。
- `expected_graduation_date`：预计毕业日期。
- `current_status`：阶段状态，例如 active、paused、completed。
- `main_theme`：阶段主线，例如“积累作品、提升身体、准备毕业”。
- `long_term_keywords`：长期关键词。
- `created_at`：创建时间。
- `updated_at`：更新时间。

### 3.3 学习记录

```json
{
  "learning_records": [
    {
      "record_id": "",
      "date": "",
      "subject": "",
      "topic": "",
      "duration_minutes": 0,
      "progress_summary": "",
      "difficulty": "medium",
      "focus_level": 3,
      "key_takeaways": [],
      "problems": [],
      "next_step": "",
      "related_goal_id": "",
      "created_at": "",
      "updated_at": ""
    }
  ]
}
```

字段说明：

- `record_id`：学习记录 ID。
- `date`：学习日期。
- `subject`：学科或领域。
- `topic`：学习主题。
- `duration_minutes`：学习时长。
- `progress_summary`：进度摘要。
- `difficulty`：难度感受，例如 easy、medium、hard。
- `focus_level`：专注程度，建议 1 到 5。
- `key_takeaways`：关键收获。
- `problems`：遇到的问题。
- `next_step`：下一步。
- `related_goal_id`：关联长期目标。
- `created_at`：创建时间。
- `updated_at`：更新时间。

### 3.4 项目进度

```json
{
  "project_progress": [
    {
      "record_id": "",
      "date": "",
      "project_id": "",
      "project_name": "",
      "progress_summary": "",
      "completed_items": [],
      "blocked_items": [],
      "decisions": [],
      "next_actions": [],
      "progress_percent": 0,
      "energy_cost": "medium",
      "created_at": "",
      "updated_at": ""
    }
  ]
}
```

字段说明：

- `record_id`：项目进度记录 ID。
- `date`：记录日期。
- `project_id`：关联项目 ID。
- `project_name`：项目名称。
- `progress_summary`：进度摘要。
- `completed_items`：已完成事项。
- `blocked_items`：阻塞事项。
- `decisions`：重要决策。
- `next_actions`：下一步行动。
- `progress_percent`：项目进度百分比。
- `energy_cost`：精力消耗，例如 low、medium、high。
- `created_at`：创建时间。
- `updated_at`：更新时间。

### 3.5 健身记录

```json
{
  "fitness_records": [
    {
      "record_id": "",
      "date": "",
      "activity_type": "",
      "duration_minutes": 0,
      "intensity": "medium",
      "body_status": "",
      "metrics": {},
      "notes": "",
      "next_plan": "",
      "created_at": "",
      "updated_at": ""
    }
  ]
}
```

字段说明：

- `record_id`：健身记录 ID。
- `date`：记录日期。
- `activity_type`：运动类型，例如 walking、running、strength、stretching。
- `duration_minutes`：运动时长。
- `intensity`：强度，例如 low、medium、high。
- `body_status`：身体状态描述。
- `metrics`：可选指标，例如体重、步数、组数、距离。
- `notes`：备注。
- `next_plan`：下一次计划。
- `created_at`：创建时间。
- `updated_at`：更新时间。

### 3.6 收入记录

```json
{
  "income_records": [
    {
      "record_id": "",
      "date": "",
      "source": "",
      "category": "",
      "amount": 0,
      "currency": "CNY",
      "status": "received",
      "related_project_id": "",
      "notes": "",
      "created_at": "",
      "updated_at": ""
    }
  ]
}
```

字段说明：

- `record_id`：收入记录 ID。
- `date`：收入日期。
- `source`：收入来源。
- `category`：收入分类，例如 part_time、freelance、scholarship、project、other。
- `amount`：金额。
- `currency`：币种。
- `status`：状态，例如 expected、received、cancelled。
- `related_project_id`：关联项目 ID。
- `notes`：备注。
- `created_at`：创建时间。
- `updated_at`：更新时间。

### 3.7 心情记录

```json
{
  "mood_records": [
    {
      "record_id": "",
      "date": "",
      "mood_score": 3,
      "mood_label": "",
      "energy_level": 3,
      "stress_level": 3,
      "summary": "",
      "triggers": [],
      "comfort_actions": [],
      "gratitude": [],
      "private": true,
      "created_at": "",
      "updated_at": ""
    }
  ]
}
```

字段说明：

- `record_id`：心情记录 ID。
- `date`：记录日期。
- `mood_score`：心情评分，建议 1 到 5。
- `mood_label`：心情标签，例如 calm、happy、tired、anxious、sad。
- `energy_level`：精力水平，建议 1 到 5。
- `stress_level`：压力水平，建议 1 到 5。
- `summary`：心情摘要。
- `triggers`：影响心情的因素。
- `comfort_actions`：有效安抚方式。
- `gratitude`：感谢或正向记录。
- `private`：是否为私密记录。
- `created_at`：创建时间。
- `updated_at`：更新时间。

## 4. 页面结构

### 4.1 总览页

用途：展示用户从 22 岁到毕业的整体成长状态。

页面模块：

- 阶段标题：22 岁到毕业。
- 距离预计毕业时间。
- 本周概览。
- 本月概览。
- 五类记录摘要。
- Roxy 的一句温和总结。
- 最近里程碑。

展示指标：

- 学习总时长。
- 项目推进数量。
- 健身次数。
- 本月收入。
- 平均心情分。

### 4.2 每日记录页

用途：录入和查看当天成长日志。

页面模块：

- 日期选择。
- 学习记录入口。
- 项目进度入口。
- 健身记录入口。
- 收入记录入口。
- 心情记录入口。
- 今日总结。

设计原则：

- 输入要轻量。
- 不强迫五项都填。
- 支持“今天只记一句话”。
- 支持 Roxy 根据对话辅助整理。

### 4.3 学习页

用途：查看学习进展和薄弱点。

页面模块：

- 学习时间趋势。
- 学科和主题列表。
- 薄弱点列表。
- 复习提醒。
- 最近学习记录。
- 下一步建议。

### 4.4 项目页

用途：追踪项目长期推进情况。

页面模块：

- 项目列表。
- 项目进度条。
- 最近完成事项。
- 当前阻塞。
- 重要决策。
- 下一步行动。

### 4.5 健身页

用途：查看运动和身体状态趋势。

页面模块：

- 本周运动次数。
- 运动类型分布。
- 运动时长趋势。
- 身体状态备注。
- 下一次健身计划。

### 4.6 收入页

用途：记录和查看收入变化。

页面模块：

- 月收入概览。
- 收入来源分布。
- 项目关联收入。
- 预期收入和已到账收入。
- 收入备注。

### 4.7 心情页

用途：记录和回顾情绪变化。

页面模块：

- 心情曲线。
- 精力曲线。
- 压力曲线。
- 高频触发因素。
- 有效安抚方式。
- 私密记录提示。

### 4.8 周报页

用途：展示每周复盘。

页面模块：

- 本周总结。
- 五类记录摘要。
- 完成事项。
- 卡住的地方。
- 下周重点。
- Roxy 的陪伴反馈。

### 4.9 月报页

用途：展示每月成长复盘。

页面模块：

- 本月主题。
- 核心成果。
- 学习进展。
- 项目进展。
- 健身趋势。
- 收入变化。
- 心情趋势。
- 下月计划。

## 5. 周报系统

### 5.1 周报目标

周报用于帮助用户每周看见自己的努力和状态，而不是进行压力评判。

周报应回答：

- 这一周我做了什么？
- 哪些事情有推进？
- 哪些地方卡住了？
- 我的身体和心情状态如何？
- 下周最重要的一两件事是什么？

### 5.2 周报数据结构

```json
{
  "weekly_reports": [
    {
      "report_id": "",
      "week_start": "",
      "week_end": "",
      "summary": "",
      "learning_summary": {},
      "project_summary": {},
      "fitness_summary": {},
      "income_summary": {},
      "mood_summary": {},
      "highlights": [],
      "blockers": [],
      "next_week_focus": [],
      "roxy_feedback": "",
      "created_at": "",
      "updated_at": ""
    }
  ]
}
```

### 5.3 周报字段说明

- `report_id`：周报 ID。
- `week_start`：周开始日期。
- `week_end`：周结束日期。
- `summary`：本周总览。
- `learning_summary`：学习摘要。
- `project_summary`：项目摘要。
- `fitness_summary`：健身摘要。
- `income_summary`：收入摘要。
- `mood_summary`：心情摘要。
- `highlights`：本周亮点。
- `blockers`：阻塞点。
- `next_week_focus`：下周重点。
- `roxy_feedback`：Roxy 的温和反馈。
- `created_at`：创建时间。
- `updated_at`：更新时间。

### 5.4 周报生成内容

学习摘要：

- 本周学习时长。
- 学习主题。
- 关键收获。
- 薄弱点。

项目摘要：

- 推进了哪些项目。
- 完成了什么。
- 卡在哪里。
- 下周下一步。

健身摘要：

- 运动次数。
- 运动时长。
- 身体状态。
- 是否需要降低或调整强度。

收入摘要：

- 本周收入金额。
- 收入来源。
- 预期收入变化。
- 与项目的关系。

心情摘要：

- 平均心情分。
- 精力水平。
- 压力水平。
- 主要触发因素。
- 有效安抚方式。

### 5.5 周报语气规则

周报应温和、诚实、可执行。

推荐风格：

- 先肯定已经完成的事。
- 再指出卡点。
- 最后给出下周最小行动。

避免风格：

- 责备用户。
- 用绩效考核语气。
- 把未完成事项描述为失败。
- 一次提出太多下周任务。

## 6. 月报系统

### 6.1 月报目标

月报用于观察更长期的变化。

月报应回答：

- 这个月我的主线是什么？
- 我在哪些方面变好了？
- 哪些模式反复出现？
- 哪些目标需要调整？
- 下个月最值得投入的方向是什么？

### 6.2 月报数据结构

```json
{
  "monthly_reports": [
    {
      "report_id": "",
      "month": "",
      "summary": "",
      "main_theme": "",
      "learning_review": {},
      "project_review": {},
      "fitness_review": {},
      "income_review": {},
      "mood_review": {},
      "milestones": [],
      "patterns": [],
      "adjustments": [],
      "next_month_goals": [],
      "roxy_message": "",
      "created_at": "",
      "updated_at": ""
    }
  ]
}
```

### 6.3 月报字段说明

- `report_id`：月报 ID。
- `month`：月份。
- `summary`：本月总结。
- `main_theme`：本月主题。
- `learning_review`：学习回顾。
- `project_review`：项目回顾。
- `fitness_review`：健身回顾。
- `income_review`：收入回顾。
- `mood_review`：心情回顾。
- `milestones`：里程碑。
- `patterns`：反复出现的模式。
- `adjustments`：需要调整的地方。
- `next_month_goals`：下月目标。
- `roxy_message`：Roxy 给用户的一段陪伴式总结。
- `created_at`：创建时间。
- `updated_at`：更新时间。

### 6.4 月报生成内容

学习回顾：

- 本月学习总时长。
- 主要学习主题。
- 已掌握内容。
- 薄弱点变化。
- 是否接近长期目标。

项目回顾：

- 项目推进情况。
- 关键成果。
- 重要决策。
- 阻塞和风险。
- 项目优先级是否需要调整。

健身回顾：

- 运动频率。
- 身体状态变化。
- 作息和精力关联。
- 下月健康重点。

收入回顾：

- 本月收入总额。
- 收入来源结构。
- 与项目、兼职或奖学金的关系。
- 下月收入机会。

心情回顾：

- 平均心情趋势。
- 压力变化。
- 精力变化。
- 高频触发因素。
- 有效恢复方式。

### 6.5 月报语气规则

月报比周报更适合做深度复盘，但仍应避免沉重。

推荐风格：

- “这个月你不是没有变化，而是在几个地方慢慢积累了。”
- “真正值得注意的是这个模式反复出现了三次。”
- “下个月我们可以把重点收窄，先抓住一件最关键的事。”

避免风格：

- 把成长简化成分数。
- 用收入或效率定义用户价值。
- 忽略心情和身体状态。
- 给出过度宏大的下月计划。

## 7. 周报和月报的关系

周报关注短周期执行，月报关注长期模式。

周报适合：

- 记录本周完成和卡点。
- 调整下周行动。
- 保持节奏。

月报适合：

- 发现趋势。
- 调整目标。
- 识别长期模式。
- 记录重要里程碑。

月报可以基于当月所有周报生成，但不应只是机械汇总。它需要提炼模式、变化和下一阶段方向。

## 8. 与 Roxy 人格系统的关系

成长日志系统应符合 Roxy 的陪伴规则：

- 温柔但诚实。
- 监督但不压迫。
- 复盘但不责备。
- 记录但不监控。
- 鼓励但不制造焦虑。

Roxy 在生成周报和月报时，应优先帮助用户看见：

- 已经完成的事。
- 正在形成的能力。
- 反复出现的困难。
- 可以降低难度的下一步。
- 值得被珍惜的变化。

## 9. 隐私和边界

成长日志包含心情、健康、收入等敏感信息，必须谨慎处理。

设计原则：

- 用户可以关闭任意记录类型。
- 用户可以删除任意记录。
- 收入和心情记录默认私密。
- 健康记录不用于医疗诊断。
- Roxy 不应把收入、健身或学习结果作为评价用户价值的依据。
- 周报和月报应允许用户手动编辑。

## 10. 页面入口建议

成长日志系统可以在产品中有三个主要入口：

- 桌宠聊天入口：用户通过对话自然记录。
- 成长日志主页：用户查看五类记录和趋势。
- 周报/月报入口：用户定期复盘。

对用户来说，最理想的体验是：

- 平时只需要轻量记录。
- 每周 Roxy 帮忙整理。
- 每月 Roxy 帮忙看见长期变化。

## 总结

成长日志系统记录的是用户从 22 岁到毕业的真实成长过程。

它关注学习、项目、健身、收入和心情五个维度，但不把用户变成被量化考核的对象。

Roxy 的任务是陪用户看见积累、整理方向、降低内耗，并在毕业前留下清晰、温柔、可回看的成长轨迹。
