# Windows 体验包构建

在专用 Python 3.10 x64 环境安装 `requirements-build.txt` 的固定依赖；勿复制开发者设置或用户目录。按 `scripts/build_beta.ps1` 创建唯一构建目录，日常版使用默认入口，跨天版加 `-StudyEdition`。版本参数需分别对应 `modules/runtime_paths.py` 的 `VERSION` 和 `STUDY_VERSION`。

```powershell
& .\scripts\build_beta.ps1 -PythonExecutable 'C:\BuildEnv\Scripts\python.exe' -ProductVersion '0.9.0-aic2026-rc.1' -BuildSlotApproved
```

原始 onedir 不直接分发。`scripts/prepare_windows_bundle.py` 核对构建哈希、裁剪原生依赖闭包，添加独立许可及对应源码，再生成便携 ZIP。`--legal-root` 可使用本 Release 便携包中的 `third_party` 目录；脚本检查五份对应源码与许可证映射的 SHA256。`--inno-license` 使用官方下载的 Inno Setup 6.7.4 原始 `License.txt`。

```powershell
python -B scripts\prepare_windows_bundle.py --build-root '<本次构建目录>' --legal-root '<已核验的third_party目录>' --inno-license '<Inno Setup目录>\License.txt' --output '<全新交付目录>\RoxyPlan'
& '<Inno Setup目录>\ISCC.exe' '/DBundleDir=<交付目录>\RoxyPlan' '/DOutputDir=<安装包输出目录>' packaging\RoxyPlan.iss
```

Inno Setup 从 [官方网站](https://jrsoftware.org/isdl.php) 获取；核对签名发布者。本项目使用非商业构建；工具许可见其原文。安装定义为当前用户安装，创建可选桌面快捷方式，不自动运行、不读取用户密钥，卸载不移除独立用户数据。这里只生成日常版安装器，跨天版以单独 ZIP 提供。

发布前核对冻结入口与版本、Qt/SSL原生加载、全部资源、包哈希和隐私；将检查结果与未验范围写入 `docs/verification/windows-release.json`。安装器未进行代码签名，不能宣称已获SmartScreen信任；本机静默安装不能代替无Python电脑的完整界面验收。
