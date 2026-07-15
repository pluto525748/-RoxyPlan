# RoxyPlan

RoxyPlan 是一个基于 Python 和 PySide6 实现的桌面成长伙伴原型，围绕学习提醒、桌面陪伴、情绪支持、本地成长记录和本地大模型调用进行验证。

当前版本为 **V0.9**。项目已从桌宠聊天 Demo 推进到具备“今日计划 -> 行动记录 -> 今日复盘 -> 成长日志”闭环的本地桌面原型，并非完整商业产品。

当前完成度总览见：[docs/current_status.md](docs/current_status.md)。

## 项目截图

![RoxyPlan Desktop Pet](docs/images/roxy_desktop_pet.png)

## 技术栈

- Python
- PySide6
- JSON 本地配置
- Ollama / Qwen 本地模型调用尝试
- Git / GitHub
- AI 辅助开发与调试

## 已实现功能

- 桌面角色显示与透明背景图片加载
- 轻量聊天窗口与中英文文本输入
- 本地提示语系统，当前包含 88 条提示语
- 独立角色人格配置
- 桌宠气泡提示
- 点击触发 `jump` 动作
- 气泡触发 `nod` 动作
- 思考状态 `thinking / shake`
- 学习提醒 `study reminder / scale`
- 睡眠状态 `sleep` 与点击唤醒 `wake`
- QThread / Worker 避免模型回复时阻塞 Qt 主线程
- `memory.example.json` 示例记忆文件与本地私有 `memory.json`
- `data/knowledge/` 本地知识库，支持读取 `.txt` / `.md`
- 聊天时基于用户问题轻量匹配本地知识片段并加入 prompt
- 多帧舞蹈素材播放框架，支持 `assets/pet/dance/dance_*.png`
- 设置面板，可调整提醒、睡眠、缩放、置顶、桌宠图片路径和模型名
- 公开仓库体检脚本，辅助检查隐私文件和临时文件误提交
- 今日计划、行动记录、今日复盘与本地成长日志
- 浅色成长面板，支持计划增删完成、行动记录和复盘保存
- `GrowthManager` 统一管理按日期隔离的私有成长数据
- `roxy.bat` 一键启动
- 基础测试脚本

## 项目结构

```text
RoxyPlan/
├─ assets/      # 桌宠图片资源
├─ data/        # 提示语、人格、桌宠配置与本地知识库目录
├─ docs/        # 产品、架构和路线文档
├─ frontend/    # 桌宠窗口、聊天界面和动作控制
├─ modules/     # 模型调用等可复用模块
└─ tests/       # 基础测试脚本
```

## Windows 运行方式

1. 创建并激活 Python 虚拟环境。
2. 安装依赖：

```powershell
pip install -r requirements.txt
```

3. 使用启动脚本：

```powershell
.\roxy.bat
```

也可以直接运行：

```powershell
.\.venv\Scripts\python.exe -u frontend\pet_app.py
```

本地模型聊天属于可选实验能力，需要用户自行准备本地 Ollama 服务与兼容模型。

## 本地记忆、知识库与成长数据

- `memory.example.json` 是公开仓库中的示例记忆结构。
- `memory.json` 是本地私人文件，程序首次启动且找不到该文件时，会根据 `memory.example.json` 自动创建。
- `memory.json` 已加入 `.gitignore`，不建议提交到 GitHub。
- 本地知识文件放在 `data/knowledge/`，支持 `.txt` 和 `.md`。
- `data/knowledge/` 中的实际知识文件默认不提交，只保留目录说明文件。
- 私有成长数据统一放在 `data/private/`。
- `data/private/today_plan.json` 保存按日期隔离的计划。
- `data/private/action_log.json` 保存行动记录，`data/private/growth_log.json` 保存每日复盘。
- `data/private/` 已整体加入 `.gitignore`，不建议提交任何计划或复盘内容。
- 首次升级时会安全导入旧 `data/*.json` 成长数据，旧文件不会被自动删除。

今日计划命令示例：

```text
添加计划：学习机器学习30分钟
查看计划
完成计划1
删除计划1
我完成了学习机器学习30分钟
记录：今天学习了逻辑回归
查看记录
今日复盘
保存今日复盘
查看成长日志
```

提交公开仓库前可以运行：

```powershell
.\.venv\Scripts\python.exe tests\check_public_repo.py
```

## 项目亮点

- 从静态聊天窗口逐步演进为带状态系统的桌宠原型
- 将提示语、人格和桌宠设置拆分为独立 JSON 数据
- 将私人记忆与公开示例记忆分离，降低误提交隐私数据的风险
- 使用轻量关键词匹配读取本地知识文件，无需向量数据库即可验证知识增强聊天
- 使用 QThread / Worker 保持模型请求期间的界面响应
- 已形成点击、气泡、思考、提醒、睡眠与唤醒的基础交互闭环
- 通过聊天命令和成长面板形成“计划 -> 行动 -> 复盘 -> 保存”的最小成长闭环
- 保持桌面端轻量实现，便于持续验证交互体验

## 当前阶段

V0.9 在桌宠交互、本地记忆和轻量知识读取基础上，加入“今日计划 -> 行动记录 -> 今日复盘 -> 成长日志”的最小闭环，并提供聊天命令与独立成长面板。现阶段仍是原型，不代表最终架构或产品完成度。

当前成长闭环已进入可试用验收状态，聊天命令和成长面板共用同一套本地私有数据。

更具体地说，现在已经完成了“桌宠入口 + 聊天窗口 + 显式本地记忆 + 规则人格 + 设置面板 + 轻量知识读取 + 本地成长闭环 + 本地模型调用实验”的原型组合；尚未完成独立后端、数据库聊天历史、长期趋势分析、周报月报、语音、复杂文档解析、向量检索、移动端和跨端同步。

## 计划中

- 持续优化聊天界面与展示效果
- 增强长期记忆规则、检索策略和隐私边界
- 改进本地知识文件读取与内容召回质量
- 增强成长日志检索，并探索周报/月报
- 加入更自然的动作表现
- 探索 Live2D、多帧动画和骨骼动画
- 探索移动端轻量访问

版本变更记录见：[docs/CHANGELOG.md](docs/CHANGELOG.md)。

## 隐私说明

本项目包含本地运行数据和模型连接设置。公开仓库提交前，应确认 `memory.json`、私人聊天记录、密钥、本地模型设置和临时测试文件均未被纳入版本控制。
