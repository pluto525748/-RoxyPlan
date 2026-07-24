# V1.8 交互状态机

`InteractionStateCoordinator` 管理对话操作状态，不管理桌宠动画状态。状态仅保存在当前进程内，并按 `conversation_id` 隔离。

## 状态

- `idle`：没有待处理交互。
- `candidates_listed`：已经列出真实业务对象，可以按编号或顺序选择。
- `awaiting_clarification`：缺少字段或目标不唯一。
- `awaiting_confirmation`：动作和不可变参数已经确定，等待确认或取消。
- `awaiting_tool_result`：已经进入安全工具执行。
- `partial_success / completed / cancelled / expired`：终态。

`确认`、`取消`、`第二个`、`就这个` 等控制表达优先交给协调器。有 pending 时普通模型不能重新解释整轮请求；同一个交互消费后不能重复执行。

## 标识边界

- `user_id`：本地用户范围，长期数据归属。
- `conversation_id`：会话边界；引用和 pending 不跨会话。
- `interaction_id`：一次澄清或确认过程。
- `tool_call_id`：一次实际工具调用。
- `resource_id`：计划、记忆或候选的真实本地标识。

新会话只清除会话引用与 pending，不删除长期记忆、候选、计划或成长数据。进程重启后危险确认失效，候选数据本身仍保留。

## 回滚

配置 `interaction_coordinator_enabled=false` 可回到旧会话控制路径，不需要迁移数据。
