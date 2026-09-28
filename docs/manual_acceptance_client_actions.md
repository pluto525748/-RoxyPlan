# V1.8.2 ClientAction 手动验收

## 准备

1. 启动 `roxy.bat`。
2. 打开聊天窗口。
3. 确认桌宠不在睡眠状态。
4. 观察终端 `[CLIENT_ACTION]`、`[ACTION_MANAGER]` 与 `[ACTION]` 日志。

## 序列一：重复舞蹈

依次输入：

1. `你好honey`：普通聊天，无舞蹈。
2. `你是谁`：普通聊天，无舞蹈。
3. `跳舞`：回复“好，我跳一小段。”，实际播放，结束后 idle。
4. `真棒`：普通聊天，无舞蹈。
5. `再跳一次`：生成新 action_id，实际播放。
6. `再跳一个舞`：生成新 action_id，实际播放。

每次动作日志的 action_id 和 idempotency_key 应不同。

## 序列二：busy

在第一段仍播放时立即再次输入 `跳舞`：

- 第二次结果为 `skipped_busy`。
- 回复为“我刚才的动作还没结束，稍等一下再跳。”
- 不重启、不叠加第二个 QTimer、不显示“第二段已开始”。
- 第一段完成后再次输入可正常播放。

## 序列三：否定与上下文

- `我不想跳舞`：无 ClientAction。
- `真棒`：无 ClientAction。
- 无舞蹈上下文时输入 `再来一个`：不执行舞蹈，应走普通追问/聊天。
- 刚完成舞蹈后输入 `再来一个`：当前规则可按最近工具上下文识别；若语义不足应澄清，不得只输出成功文字。

## 失败验收

- 缺少素材：Dispatcher/Manager 返回失败，不显示成功跳舞文案。
- 过期动作：返回 expired。
- 同一 action_id 重放：只执行一次，第二次 skipped_duplicate。
- 关闭窗口或主动取消动作：最终状态 idle。

## 自动化对应

- `tests/test_dance_repeat.py`
- `tests/test_client_action_dispatcher.py`
- `tests/test_pet_action_state_machine.py`
- `tests/test_client_action_claim_guard.py`
- `tests/test_agent_response_client_actions.py`
- `tests/test_action_runtime_entrypoints.py`
