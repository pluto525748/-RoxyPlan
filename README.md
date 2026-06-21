# RoxyPlan

RoxyPlan 是一个基于 Python 和 PySide6 实现的桌面端 AI 交互 Demo，围绕学习提醒、桌面陪伴、情绪支持和本地大模型调用进行原型验证。

项目当前处于 **V0.7 原型阶段**，用于验证桌面角色、轻量聊天、状态动作和本地模型交互体验，并非完整商业产品。

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
- `roxy.bat` 一键启动
- 基础测试脚本

## 项目结构

```text
RoxyPlan/
├─ assets/      # 桌宠图片资源
├─ data/        # 提示语、人格与本地运行数据
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

## 项目亮点

- 从静态聊天窗口逐步演进为带状态系统的桌宠原型
- 将提示语、人格和桌宠设置拆分为独立 JSON 数据
- 使用 QThread / Worker 保持模型请求期间的界面响应
- 已形成点击、气泡、思考、提醒、睡眠与唤醒的基础交互闭环
- 保持桌面端轻量实现，便于持续验证交互体验

## 当前阶段

V0.7 聚焦桌宠交互系统和本地模型聊天验证。现阶段仍有原型代码、调试日志和简化的数据处理方式，不代表最终架构或产品完成度。

## 后续计划

- 持续优化聊天界面与展示效果
- 增强长期记忆规则和隐私边界
- 改进本地知识文件读取
- 加入更自然的动作表现
- 探索 Live2D、多帧动画和骨骼动画
- 探索移动端轻量访问

## 隐私说明

本项目包含本地运行数据和模型连接设置。公开仓库提交前，应确认个人记忆、聊天记录、密钥、本地模型设置和临时测试文件均未被纳入版本控制。
