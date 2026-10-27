# 美股 K 线复盘训练器

本仓库基于 [fat7/KLinePlayground](https://github.com/fat7/KLinePlayground) 修改，保留中文界面和本地训练流程，行情数据切换为美股历史日线。

## 功能

- 指定股票与日期训练；盲盒模式从内置的美股样例列表抽取标的。
- 通过 Yahoo Finance 的 `yfinance` 获取历史行情，也可导入本地离线行情。
- 提供 K 线、成交量、常见技术指标、模拟交易和训练报告。
- 支持 AI 复盘功能；需要自行配置兼容 OpenAI 的 API。

## 运行

Python 3.10 或更新版本：

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m backend.app_enhanced
```

在浏览器打开 `http://127.0.0.1:5000`。桌面版可安装 `webview_app/requirements_pywebview.txt` 后运行 `python webview_app/main_pywebview.py`。Windows 打包流程见 [BUILD_GUIDE.md](BUILD_GUIDE.md)。

Windows x64 便携版可在 [GitHub Releases 最新版本](https://github.com/oswaldxdd/KLinePlayground/releases/latest) 下载。下载 `KLinePlayground-US-v3.3.0-us.2-win-x64.zip` 后请完整解压，再双击“启动美股训练器.cmd”。仓库中保留的旧版 `.exe` 是上游 A 股版本，不是当前美股版。

桌面版需要 Windows x64 和 Microsoft Edge WebView2 Runtime。若窗口未能启动，程序会显示错误摘要；诊断日志默认在解压目录 `data/logs/`，该目录不可写时会写入 `%LOCALAPPDATA%/KLinePlayground-US/logs/`。

## 数据与账户文件

- 在线缓存：`data/us_market/`
- 离线美股行情：`data/us_market_offline/`
- 美股用户设置与训练记录：`users_us/`

这些目录与上游 A 股缓存和用户数据隔离。美股训练不会读取或迁移旧 A 股记录。

离线行情 CSV 放在 `data/us_market_offline/`，文件名使用代码，例如 `AAPL.csv`。最少需要 `date,open,high,low,close,volume` 六列，日期格式使用 `YYYY-MM-DD`；例如：

```csv
date,open,high,low,close,volume
2024-01-02,185.64,188.44,183.89,185.64,82488700
```

## 数据与模拟限制

- 在线数据使用 Yahoo Finance 的非官方 `yfinance` 接口，需联网；覆盖范围、限流、网络状态和历史数据可用性由上游决定。当前仅按美股 USD 标的和纽约交易所时区筛选，不提供实时行情保证。
- Yahoo 返回的历史 OHLC 已进行拆股调整；股息复权关闭，避免回放过去时引用后续股息信息。数据源调整后仍可能修订历史价格，不处理股息现金、拆股派发等公司行动结算。
- 模拟器使用整股、按日线收盘价成交、同一模拟交易日可买卖；不支持碎股、做空、保证金、真实结算流程或完整券商费用。佣金及额外卖出费率默认为零，只是可配置的训练参数。
- 本项目仅供学习和研究，不构成投资建议。使用行情数据时请遵守 Yahoo Finance 的适用条款。

## 验证

```powershell
.venv\Scripts\python.exe -m unittest discover -s tests -v
node --check frontend/js/main_enhanced.js
```

## 许可证

本项目遵循仓库中的 [MIT License](LICENSE)。
