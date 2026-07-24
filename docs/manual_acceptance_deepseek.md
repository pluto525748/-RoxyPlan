# DeepSeek V1.7 手动验收

真实 API 测试默认跳过，Codex 不需要也不应接收你的 Key。验收前先确认 DeepSeek 账户可用，并保留 Ollama `qwen3:4b` 作为降级。

## 启动

```powershell
cd <你的 RoxyPlan 项目目录>
.\.venv\Scripts\python.exe -u frontend\pet_app.py
```

Local Web：

```powershell
.\run_roxy_web.bat
```

## 清单

1. 暂不配置 Key，桌面端可以启动，设置页显示 DeepSeek 未配置。
2. 确认 Ollama 已启动时，普通“你好”可以得到本地回复。
3. 在设置页填写自己的 Key，输入默认被遮挡，保存后重开设置不显示完整值。
4. 点击“测试 DeepSeek 连接”，窗口保持可拖动，最终显示连接正常或受控错误。
5. 选择“自动”模式，普通问答走默认模型。
6. 输入较长规划或复杂复盘，状态与统计显示复杂模型路由；回复中不出现内部思考。
7. 输入“把学习机器学习50分钟加入今天计划”，确认数据实际新增后才出现完成式回复。
8. 输入模糊愿望“我想找时间学机器学习”，应澄清或确认，不能静默写入。
9. 输入“看看今天计划”，应走确定性本地工具，不依赖模型自由回答。
10. 输入“把刚才那个改成50分钟”，目标明确时更新；目标不明确时要求澄清。
11. 输入同时包含添加计划和记录行动的明确请求，按顺序执行且两个结果都真实存在。
12. 请求删除计划或记忆，应要求二次确认，取消后数据保持不变。
13. 在 DeepSeek 请求期间断网，普通问答可降级 Ollama，不能虚构计划已经写入。
14. 同时停止 DeepSeek 与 Ollama，聊天显示可理解错误，计划查看等本地工具仍可用。
15. 清除私有 Key 后，状态变为未配置；若环境变量仍存在，应继续显示由环境变量配置。
16. 关闭并重启程序，私有 Key 和非敏感模型设置按预期保留。
17. 打开 Local Web 状态面板，显示 Provider、模型、模式和降级状态，但不显示 Key。
18. 设置页本地用量统计随请求增加，清空后归零；文件中没有 prompt 或回复正文。

## 控制台观察

建议关注但不要求逐字一致：

```text
[Tool] selected: add_plan
[Tool] execute: add_plan
[Tool] success: add_plan
[ActionClaimGuard] blocked unverified completion claim
```

不要在截图或问题反馈中包含真实 Key、完整私有记忆、私人聊天记录或 `data/private/` 内容。

## 回滚为纯 Ollama

在设置中关闭“在线模型”，保留“本地降级”，并确认 Ollama 模型为 `qwen3:4b`。也可以清除私有 Key 和 `DEEPSEEK_API_KEY` 后重启。该操作不会删除计划、记忆、成长数据或聊天历史。
