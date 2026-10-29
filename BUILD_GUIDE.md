# Windows x64 发布构建

本仓库当前发布目标为 **KLinePlayground 美股 K 线复盘训练器 v3.3.0-us.3**。源码版本与 Windows 文件属性版本对应 `3.3.0-us.3` / `3.3.0.3`。

## 构建环境

- Windows x64，Python **3.12.10 x64**。
- Microsoft Edge WebView2 Runtime（运行目标机需要；构建机使用系统 runtime）。
- 构建依赖和全部解析后版本固定在 `requirements-release-win.lock.txt`。
- PyWebView 使用 Windows 原生 WebView2 与 pythonnet；不安装 Qt 或 CEF。目标电脑仍需 Microsoft Edge WebView2 Runtime。
- 主程序与 AI 助手使用 PyInstaller `onefile`。Python.NET 和 WebView2 托管支持程序集包含在各自 EXE 中，避免 Windows 资源管理器解压下载 ZIP 时把 Internet 来源标记传播到这些运行时文件并阻止 .NET 加载。下载的 EXE 本身仍受 Windows SmartScreen、Defender 和组织策略检查；发布包不关闭或调整这些系统保护。

从仓库根目录执行：

```powershell
py -3.12 -m venv ..\.venv
..\.venv\Scripts\python.exe -m pip install -r requirements-release-win.lock.txt
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts\build_us_release.ps1
```

构建脚本默认使用仓库父级的 `..\.venv\Scripts\python.exe`。如在其他环境构建，可传入 `-PythonExe` 指定 Python 3.12.10 x64；例如：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts\build_us_release.ps1 -PythonExe 'C:\path\to\python.exe'
```

构建脚本验证 Python/架构、固定工具链、pythonnet/EdgeChromium 模块，并拒绝包含 Qt 的构建环境。正式构建要求干净 Git 工作区；本地验证可以传 `-AllowDirty`，但产物清单会标记未提交源码，不能作为正式发行件。

## 输出与检查

产物写入仓库父级 `release-builds/` 的唯一构建目录，不清理或覆盖旧输出。ZIP 中包含：

- `bin/KLineTrainer_US.exe`：单文件美股训练器。
- `bin/KLineTrainer_AI_Assistant.exe`：单文件独立 AI 助手；构建或 smoke 未通过时整体构建失败，不发布缺少助手的 ZIP。
- `data/`、`users_us/`：首次运行写入的行情、设置、会话和用户训练数据目录。
- `data/logs/`：启动日志；目录不可写时回退到 `%LOCALAPPDATA%/KLinePlayground-US/logs/`。
- `README.txt`、上游 `LICENSE`、`DEPENDENCIES.lock.txt`、`THIRD_PARTY_LICENSES/`、`release-manifest.json`。
- ZIP 旁边的 `.sha256.txt` 文件。

压缩前，两个冻结程序分别以 `--smoke` 无界面运行。主程序会检查静态页、yfinance 模块/数据源、离线训练、买入、同日卖出和结束报告接口；助手检查静态页与 Flask 健康接口。Smoke 只使用临时数据，不联网，也不把测试日志、AI Key 或用户数据放入 ZIP。最终仍应在干净的独立解压目录启动 GUI，检查 Edge WebView2 实际窗口、启动日志路径，以及 AI 助手能否发现主会话。发布回归还要验证被 Windows 标记为 Internet 来源的 ZIP 在资源管理器完整解压后可启动；不要清除文件来源标记或更改系统安全策略来替代验证。

PowerShell 返回的 SHA256 也保存在 ZIP 旁边，例如：

```powershell
Get-FileHash .\KLinePlayground-US-v3.3.0-us.3-win-x64.zip -Algorithm SHA256
```

`install.bat` 现在只调用这个安全构建脚本；不要使用上游旧版安装包或旧 EXE 作为本次美股发布件。
