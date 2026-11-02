# 开源依赖、参考与自研边界

## 自研范围

RoxyPlan 的业务编排、交互状态、工具校验、正式记忆治理、成长记录、上下文组装、桌面界面和测试工具由本项目维护，按MIT发布。采用通行的Agent设计范式和开源依赖，不主张独占这些范式；开发中使用了AI辅助，不是模型训练项目。

## 依赖

| 资源 | 当前版本／来源 | 许可与用途 |
|---|---|---|
| Python | 开发／构建验证为3.10.20 | PSF；标准库、运行环境，源码仓库不包含解释器 |
| PySide6 / Qt / shiboken6 | 6.4.2，[Qt for Python](https://doc.qt.io/qtforpython-6/licenses.html) | 采用适用LGPLv3路径，另有上游GPL／商业许可选项；桌面组件及动态库，不被MIT重新许可 |
| Pillow | 运行声明 `>=10,<13`，既有构建锁12.3.0 | 上游Pillow许可（HPND体系）；图像工具依赖 |
| FastAPI / Uvicorn | 开发验证0.103.2 / 0.23.2 | MIT / BSD-3-Clause；可选Local Web适配，不是公网服务 |
| pytest / HTTPX | 开发验证9.1.1 / 0.24.1 | MIT / BSD-3-Clause；离线测试与Web测试工具 |
| PyInstaller | 6.22.3，hooks 2026.8 | GPL及bootloader例外／适用hook许可；构建工具，不将本项目代码改为GPL |
| Inno Setup | 6.7.4，[官方许可](https://jrsoftware.org/files/is/license.txt) | Windows安装器生成工具，本项目非商业构建；原始许可随包保留 |
| DeepSeek / Ollama | 见模型配置文档 | 外部模型服务／运行服务；不随仓库分发Key或模型权重，不将服务条款写成MIT |
| 桌宠图集 | ga hing / yznzl，见 [NOTICE](NOTICE) | 独立个人非商业范围，见 [素材许可](ASSET_LICENSE.md)，不属于MIT资产 |

Git 仓库不提交Qt、Python或编译产物。Release 单独提供 Windows 安装包与便携包：其中包含 Python、Qt 动态运行依赖、原始许可证与五份对应 Qt/PySide 源码归档，按实际文件清单核验；`third_party/` 提供来源、哈希及动态库替换说明。Qt PDF/PDFium、Virtual Keyboard、Quick/QML 与可选软件 OpenGL 不在保留的原生依赖闭包中。微软运行库由用户从微软官方获取，不随包复制。完整检查范围见 [windows-release.json](docs/verification/windows-release.json)，不以本机检查冒充干净机验收。

## 数据及研究参考

项目中文语料主要为自建场景、受控变体、已有测试和脱敏故障。CheckList、Contrast Sets及中文省略／对话研究用于方法组织，来源详见 [测试说明](tests/README.md) 和 [语料索引](tests/fixtures/beta_interaction_regressions.index.json)。MASSIVE、CLINC150仅有调研记录，未将其全集导入训练；不得把自建语料称为真实用户数据或开源训练集。

知识目录支持已有TXT/Markdown轻量读取，但本次比赛主线不使用知识库，不把它描述为已验证的RAG学习成果。
