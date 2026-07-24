# API Key Security

## 读取优先级

1. 环境变量 `DEEPSEEK_API_KEY`。
2. 本地私有文件 `data/private/llm_secrets.json`。
3. 桌面设置页输入后保存到上述私有文件。

`data/private/`、`.env` 和密钥文件已被 Git 忽略。公开仓库只包含 `.env.example` 占位符。

## 配置方式

### 环境变量

```powershell
setx DEEPSEEK_API_KEY "你自己的Key"
```

执行后重新打开终端或重新登录，再启动 RoxyPlan。不要把 Key 写入命令截图、Issue 或聊天记录。

### 桌面设置

打开“设置 -> 在线模型服务”，填写 DeepSeek API Key，保存后点击“测试 DeepSeek 连接”。输入框默认使用密码掩码，留空保存会保留现有私有 Key；“清除”只清除私有文件，不修改系统环境变量。

### 私有文件

兼容文件位于：

```text
data/private/llm_secrets.json
```

它只应保存在本机。不要手工复制到公开配置、`memory.json`、知识库或 README。

## 不会保存的位置

- `data/pet_config.json`
- `config.json`
- `memory.json`
- 聊天历史和会话摘要
- 模型使用统计
- Web API 响应和服务日志

Local Web 只返回 `api_key_configured: true/false`。当前网页不提供 Key 输入，避免在浏览器中长期保存密钥。

## 连接测试

桌面连接测试运行在 QThread，不阻塞 Qt 主线程。失败信息只显示状态和受控错误码，不包含 Key、HTTP 请求头、堆栈或本地绝对路径。

## 泄露应对

如怀疑 Key 泄露，应立即在 DeepSeek 控制台撤销旧 Key、创建新 Key，并清除本地私有文件或环境变量。仅删除 Git 历史中的文本不能使已泄露 Key 恢复安全。
