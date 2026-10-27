美股 K 线复盘训练器 v3.3.0-us.2（Windows x64）

【启动】
双击“启动美股训练器.cmd”。也可运行 bin\KLineTrainer_US.exe。
双击“启动AI助手.cmd”运行独立助手（bin\KLineTrainer_AI_Assistant.exe）。

【运行要求】
需要 Windows x64 和 Microsoft Edge WebView2 Runtime。程序使用系统 WebView2，不包含浏览器运行时；不需要另装 Python。主程序和 AI 助手各为一个独立 EXE；窗口内运行所需的 Python.NET 与 WebView2 支持组件嵌入 EXE，Windows 解压 ZIP 时附加的来源标记不会阻止这些内部组件加载。Windows 对下载 EXE 本身的 SmartScreen、Defender 和组织策略检查仍然生效，请按系统提示核验发布来源；不要关闭或降低 Windows 安全设置。

【启动诊断】
启动日志默认写入 data\logs\KLineTrainer_US-startup.log 或 data\logs\KLineTrainer_AI_Assistant-startup.log。若数据目录不可写，日志改写到 %LOCALAPPDATA%\KLinePlayground-US\logs。启动失败时会显示错误摘要和日志位置。
首次启动需要联网下载 Yahoo Finance 历史日线。Yahoo 覆盖、限流和网络状态可能导致请求失败。

【数据位置】
data\ 保存美股行情缓存、AI 设置及连接信息；users_us\ 保存本机用户设置、训练历史和交易记录。
请整体备份这些目录。不要把个人数据或 API Key 分享到公开位置。

【AI 助手（仅在随包提供时使用）】
先启动主训练器，创建用户并开启训练；在设置中开启 AI/API 接入后，再启动 AI 助手。
在助手中设置兼容 OpenAI 的 API 地址、模型和 Key，然后检查连接并单次分析。
Key 仅做本地混淆保存，不是加密保险箱。不要在共用电脑保存敏感凭证。

【训练限制】
这是历史行情训练器，不是券商软件。仅支持整股、日线收盘价和同一模拟日买卖；不模拟碎股、做空、保证金、真实结算、完整券商费用、股息现金或公司行动派发。佣金与额外卖出费率默认为零。
Yahoo 历史 OHLC 已做拆股调整，股息复权关闭。只供学习和研究，不构成投资建议。

【文件】
LICENSE 为本项目 MIT 许可证。DEPENDENCIES.lock.txt 记录精确构建依赖；THIRD_PARTY_LICENSES/ 和 THIRD_PARTY_LICENSES.txt 提供可从发行包元数据收集到的第三方许可证。
release-manifest.json 记录源码 SHA、构建版本和平台信息；旁边的 .sha256.txt 可校验 ZIP 完整性。
