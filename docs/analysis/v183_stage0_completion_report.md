# RoxyPlan V1.8.3 阶段 0 完成报告

> 执行日期：2026-07-29
>
> 阶段性质：固化可运行基线、恢复本地环境、隔离测试数据
>
> 边界：未修改业务行为、路由、人格、上下文策略或私有数据

## 总结

阶段 0 已完成：当前 V1.8.3 工作架构已建立可回滚 Git 基线，pytest 已改为使用临时数据仓库并在会话级保护真实私有目录，本地 Python 3.10 环境已按项目依赖恢复。完整测试为 `411 passed`，受保护私有文件在执行前后 `added=0`、`removed=0`、`changed=0`。

## 1. 外部备份位置

外部备份目录：

`C:\Users\16127\Projects\Roxyplan-backups`

关键备份：

- `Roxyplan_v1.8.3_tracked_20260729_165439.patch`：初始 tracked 工作区补丁。
- `Roxyplan_v1.8.3_untracked_20260729_165439.txt`：初始 untracked 清单。
- `Roxyplan_v1.8.3_git_status_20260729_165439.txt`：初始 Git 状态。
- `Roxyplan_v1.8.3_git_log_20260729_165439.txt`：初始 Git 日志。
- `Roxyplan_v1.8.3_untracked_backup_20260729_165439/`：75 个初始 untracked 文件的只读副本，保留相对目录；未复制 `.venv`、私有数据、密钥或缓存。
- `Roxyplan_stage0_private_baseline_20260729_165439.json`：测试前真实私有数据文件清单和 SHA-256 基线。

私有数据基线文件自身的 SHA-256：

`D1CF65C30A0E3772C6C25802D91A2FEC74AA8139AB83559FFC93F391B41B1546`

## 2. 当前分支

当前阶段分支：

`baseline/v1.8.3-working-state`

该分支从阶段 0 开始前的 `dad4ff2429d52f307c3f17c24b6dddc9a5576f87` 建立，未改名项目根目录。

## 3. 工作架构基线提交

提交：

`dbe0d9914a701422d67b5d8d651ae4559c487f13 chore: preserve V1.8.3 working architecture baseline`

该提交固化了恢复后工作区中的 V1.8.2/V1.8.3 已完成架构、文档、测试和 benchmark 必要资产。提交规模为 85 个文件；提交前没有 staged diff。

## 4. 测试隔离提交

提交：

`b3612c8737a324c163e018d0c1179eaf73e93f9e test: isolate memory repositories from real private data`

该提交仅包含 `.gitignore` 和 `tests/` 下 22 个文件，共 23 个文件；没有修改 `frontend/`、`modules/`、`server/` 业务代码或任何私有配置。

## 5. 纳入基线的主要文件

纳入范围包括：

- 桌面端现有入口、动作分发与动作状态管理。
- 统一会话服务、语义解析、交互状态、动作契约和工具执行链。
- Local Web 对现有核心模块的适配层。
- 当前架构、协议、feature flags、验收、技术债和版本完成文档。
- 中文 NLU benchmark 的固定语料、schema、生成/运行脚本、聚合迭代报告与最终报告。
- 与当前行为对应的回归测试和阶段 0 文件清单。

逐文件分类见 `docs/analysis/v183_baseline_file_inventory.md`。

## 6. 排除的运行时和私有内容

以下内容未进入 Git 提交：

- `.venv/`、pytest/coverage 临时目录、缓存、日志、临时文件和锁文件。
- 根目录 `memory.json`、`config.json`、`.env` 及密钥类文件。
- `data/private/` 全目录。
- 本地 today plan、action、growth、pet config 等运行时状态。
- 私有知识目录及本机绝对路径、API key、token、password。
- 16 个用途未确认的详细 train/dev benchmark 运行报告。

暂存补丁执行过本机路径和高置信凭据模式扫描，未发现真实凭据或本机用户目录路径。

## 7. C 类文件及处理方式

以下 16 个 benchmark 详细报告被分类为 C：保留在本地工作区和外部只读备份中，不删除、不提交，也不加入忽略规则，等待后续明确用途。

```text
tests/nlu_benchmark/reports/iteration_01_dev.json
tests/nlu_benchmark/reports/iteration_02_dev.json
tests/nlu_benchmark/reports/iteration_03_dev.json
tests/nlu_benchmark/reports/iteration_04_dev.json
tests/nlu_benchmark/reports/iteration_04_train.json
tests/nlu_benchmark/reports/iteration_05_dev.json
tests/nlu_benchmark/reports/iteration_06_dev.json
tests/nlu_benchmark/reports/iteration_07_train.json
tests/nlu_benchmark/reports/iteration_08_dev.json
tests/nlu_benchmark/reports/iteration_08_train.json
tests/nlu_benchmark/reports/iteration_09_dev.json
tests/nlu_benchmark/reports/iteration_09_train.json
tests/nlu_benchmark/reports/iteration_10_dev.json
tests/nlu_benchmark/reports/iteration_10_train.json
tests/nlu_benchmark/reports/iteration_10b_dev.json
tests/nlu_benchmark/reports/iteration_10b_train.json
```

## 8. 测试污染根因

根因不是单个测试遗漏清理，而是测试只替换了部分文件路径，相关对象仍通过默认参数回落到项目真实数据目录：

- `MemoryManager` 即使收到临时 `memory.json`，默认 backup/conflict/audit 路径仍可能指向真实 `data/private`。
- `MemoryCandidateManager` 若只替换 candidate 文件、未注入 repository，会回落到真实 memory/conflict/backup/lock 路径。
- repository 的锁目录由 conflict 文件父目录派生，因此不完整替换会继续在真实目录创建锁。
- 桌面窗口测试此前隔离 memory/history，但默认 `GrowthManager` 仍可能连接真实成长数据。
- 设置和主动服务测试中的默认 `SecretStore`、`ModelUsageStore` 仍可能连接真实配置目录。

历史污染文件未自动删除。阶段 0 将测试执行与现有真实目录完全隔离，并把现状作为审计基线保护。

## 9. fixture 与 repository 隔离方案

新增的测试支持层执行以下约束：

- 每个相关测试使用 `tmp_path` 创建完整的 memory、candidate、conflict、audit、backup 和 lock 目录结构。
- `MemoryService`、`MemoryManager`、`MemoryCandidateManager` 显式共享同一个临时 `LocalJsonMemoryRepository`。
- growth、chat history、secret store、model usage store 均由测试显式注入临时路径。
- pytest 会话启动时安装构造器路径保护；任何测试若把上述 repository/store 指向项目真实私有路径，会在构造器执行真实 I/O 前抛出 `RepositoryPathViolation`。
- 会话结束后恢复被包装的构造器，不改变生产代码。

## 10. 快照保护范围

会话保护覆盖：

- 根目录 `memory.json`、`config.json`。
- `data/private/` 全目录，包括 candidate、conflict、audit、backup 和 lock。
- legacy today plan、action、growth 数据。
- `data/pet_config.json` 与 `data/knowledge/`。
- `.env` 和已知密钥文件候选。

文件 SHA-256 与大小是内容变化的主要判据，mtime 仅作辅助记录；目录和符号链接也进入会话清单。快照发现变化时只报告差异，不自动删除或回写真实数据。

## 11. 私有数据执行前后结果

外部文件基线与完整测试后的独立对比：

```text
baseline_files=1352
current_files=1352
added=0
removed=0
changed=0
```

pytest 扩展保护清单共 `1367` 条记录，其最终 manifest SHA-256 为：

`11b9d26ea94c1258dbbdda70a738b5ec84fbb4fbb1894b338fd10562b5557733`

两种计数口径不同：外部基线统计已有文件；pytest 清单还记录受保护目录和其他候选类型。两项检查均确认真实私有数据未被测试改变。

## 12. 完整测试结果

最终完整回归：

```text
411 passed in 53.34s
```

另外完成：

- `411 tests collected in 1.23s`，collection warnings 作为错误处理时仍通过。
- 隔离相关目标测试：`154 passed in 26.55s`。
- 修复最后几处默认路径注入后的目标回归：`19 passed in 8.12s`。
- 162 个 Python 文件内存编译检查通过。
- 桌面入口导入通过，未创建 `QApplication` 实例。
- Web 入口导入通过，且未加载 PySide6。

测试临时目录显式放在项目外：

`C:\Users\16127\Projects\Roxyplan-test-temp\stage0_20260729_171031_703`

## 13. Python 与依赖环境

项目根目录 `.venv` 已恢复为：

```text
Python 3.10.20 (Anaconda build, x64)
PySide6 6.4.2
Pillow 12.3.0
FastAPI 0.103.2
uvicorn 0.23.2
httpx 0.24.1
pydantic 1.10.15
```

已在 `.venv` 中执行并确认 `requirements.txt` 与 `server/requirements.txt` 全部满足；PySide6、Pillow、桌面入口和 Web 入口导入成功。没有升级依赖，也没有修改依赖或配置文件。

`roxy.bat` 的启动链为项目根目录 `.venv\Scripts\python.exe -> frontend\pet_app.py`，解释器和目标文件均存在、可导入。为避免启动 GUI 时触碰真实运行时数据，本阶段采用静态链路、导入和完整测试验证，没有进行持久交互式桌面会话。

## 14. 警告处理

原有 5 条 pytest collection warning 来自名称以 `Test` 开头、但带构造器的测试时钟辅助类。测试辅助类已重命名为 `FakeClock`，不改变生产行为。最终 collection 和完整测试没有警告。

## 15. Git 工作区状态

在隔离提交完成后：

- tracked 和 staged 工作区为空。
- 仅保留第 7 节列出的 16 个 C 类 untracked 报告。
- 私有/运行时文件继续由已有和补充的 `.gitignore` 规则排除。

本完成报告提交后应保持同一状态：除 16 个有意保留的 C 类文件外无工作区改动。

## 16. GitHub 推送状态

未推送。阶段 0 任务明确禁止 push；当前基线仅保存在本地仓库的以下分支：

```text
origin: https://github.com/pluto525748/-RoxyPlan.git
branch: baseline/v1.8.3-working-state
```

未创建 Pull Request，未合并 `main`，也未向远端写入任何分支。若后续需要发布，应在用户单独明确授权后，先复核提交范围和私有数据排除规则，再执行推送。

## 17. 下一阶段起点

下一阶段应从 `baseline/v1.8.3-working-state` 的阶段 0 最终 HEAD 新建工作分支，不直接在基线分支继续功能开发。建议分支名：

`feature/v1.9-safe-interaction-baseline`

下一阶段开始前仍应先复核本报告、`docs/analysis/v183_baseline_file_inventory.md` 和 `docs/analysis/persona_memory_continuity_architecture_review.md`，再按已批准范围实施。

## 18. 回滚方法

优先使用非破坏方式：

1. 查看阶段 0 开始前状态：从 `dad4ff2429d52f307c3f17c24b6dddc9a5576f87` 创建新的 recovery 分支，不覆盖当前工作区。
2. 只查看已固化架构、不含测试隔离：检出或从 `dbe0d9914a701422d67b5d8d651ae4559c487f13` 新建分支。
3. 保留架构基线但撤销隔离修复：在单独 recovery 分支上 revert `b3612c8737a324c163e018d0c1179eaf73e93f9e`，不要 hard reset。
4. Git 记录不足时，使用第 1 节外部 patch、状态清单和只读 untracked 备份重建阶段 0 前工作区。
5. 任何回滚都不得自动删除或覆盖 `data/private/`、`memory.json` 等真实数据；先与外部私有数据快照比较，再由用户明确决定数据操作。

阶段 0 的核心保证是：Git 基线可追溯、测试运行可隔离、真实数据可验证且未被更改。
