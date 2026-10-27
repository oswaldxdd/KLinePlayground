# Windows x64 发布构建

本仓库当前发布目标为 **KLinePlayground 美股 K 线复盘训练器 v3.3.0-us.1**。源码版本与 Windows 文件属性版本对应 `3.3.0-us.1` / `3.3.0.1`。

## 构建环境

- Windows x64，Python **3.12.10 x64**。
- Microsoft Edge WebView2 Runtime（运行目标机需要；构建机使用系统 runtime）。
- 构建依赖和全部解析后版本固定在 `requirements-release-win.lock.txt`。
- PyWebView 使用 Windows 原生 WebView2 与 pythonnet；不安装 Qt 或 CEF。

从仓库根目录执行：

```powershell
py -3.12 -m venv ..\.venv
..\.venv\Scripts\python.exe -m pip install -r requirements-release-win.lock.txt
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts\build_us_release.ps1
```

构建脚本验证 Python/架构、固定工具链、pythonnet/EdgeChromium 模块，并拒绝包含 Qt 的构建环境。正式构建要求干净 Git 工作区；本地验证可以传 `-AllowDirty`，但产物清单会标记未提交源码，不能作为正式发行件。

## 输出与检查

产物写入仓库父级 `release-builds/` 的唯一构建目录，不清理或覆盖旧输出。ZIP 中包含：

- `bin/KLineTrainer_US/`：美股训练器 EXE 和 PyInstaller onedir 运行库。
- `bin/KLineTrainer_AI_Assistant/`：独立 AI 助手 EXE 和其运行库；只有该组件通过自己的 smoke 时才纳入。
- `data/`、`users_us/`：首次运行写入的行情、设置、会话和用户训练数据目录。
- `README.txt`、上游 `LICENSE`、`DEPENDENCIES.lock.txt`、`THIRD_PARTY_LICENSES/`、`release-manifest.json`。
- ZIP 旁边的 `.sha256.txt` 文件。

压缩前，两个冻结程序分别以 `--smoke` 无界面运行。主程序会检查静态页、yfinance 模块/数据源、离线训练、买入、同日卖出和结束报告接口；助手检查静态页与 Flask 健康接口。Smoke 只使用临时数据，不联网，也不把测试日志、AI Key 或用户数据放入 ZIP。最终仍应在干净的独立解压目录启动 GUI，检查 Edge WebView2 实际窗口和 AI 助手能否发现主会话。

PowerShell 返回的 SHA256 也保存在 ZIP 旁边，例如：

```powershell
Get-FileHash .\KLinePlayground-US-v3.3.0-us.1-win-x64.zip -Algorithm SHA256
```

`install.bat` 现在只调用这个安全构建脚本；不要使用上游旧版安装包或旧 EXE 作为本次美股发布件。
