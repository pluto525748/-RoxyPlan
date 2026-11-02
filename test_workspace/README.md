# RoxyPlan 测试工作区

这里是测试运行时产物的统一隔离区，不是正式业务数据目录。

## 目录规则

- `runs/<run-id>/pytest/`：本次 pytest 的 `basetemp`。
- `runs/<run-id>/temp/`：Python `tempfile`、Qt 和子进程使用的临时目录。
- `runs/<run-id>/artifacts/`：需要保留的测试报告、日志、截图或隔离数据。
- `runs/` 中除 `.gitkeep` 外的内容全部由 Git 忽略。

请通过项目根目录的 `scripts/run_isolated_tests.ps1` 运行 pytest。脚本会为每次运行创建唯一目录，禁用 pytest cache，并把 `TEMP`、`TMP` 和 pytest `basetemp` 指向该目录。

成功运行默认删除对应 run；使用 `-KeepRun` 可以保留成功现场。失败运行会自动保留，完成诊断后只精确删除对应 run，不要对项目根目录执行批量清理。

该目录不能保存或复制正式 `memory.json`、`config.json`、`data/private/`、`data/pet_config.json`、API Key 或其他私人数据。测试仍必须使用 `tests/conftest.py` 的完整隔离数据根和正式路径防火墙。
