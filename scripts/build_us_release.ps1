param(
    [string]$Version = "v3.3.0-us.1",
    [string]$OutputRoot = "",
    [switch]$AllowDirty
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$workspaceRoot = Split-Path -Parent $repoRoot
$python = Join-Path $workspaceRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python)) {
    throw "构建虚拟环境不存在：$python。先安装 requirements-release-win.lock.txt。"
}

$sourceSha = (& git -C $repoRoot rev-parse HEAD).Trim()
if ($LASTEXITCODE -ne 0 -or -not $sourceSha) { throw "无法读取当前 Git 源码 SHA。" }
$dirtyPaths = @(& git -C $repoRoot status --porcelain --untracked-files=all)
$dirty = $dirtyPaths.Count -gt 0
if ($dirty -and -not $AllowDirty) {
    throw "工作区存在未提交修改。正式构建要求干净工作区；仅在本地验证时传入 -AllowDirty。"
}

if (-not $OutputRoot) { $OutputRoot = Join-Path $workspaceRoot "release-builds" }
$OutputRoot = [System.IO.Path]::GetFullPath($OutputRoot)
$stamp = [DateTime]::UtcNow.ToString("yyyyMMddTHHmmssZ")
$dirtyTag = if ($dirty) { "-dirty" } else { "" }
$buildRoot = Join-Path $OutputRoot "$Version-$($sourceSha.Substring(0, 12))$dirtyTag-$stamp"
$packageName = "KLinePlayground-US-$Version-win-x64"
$packageRoot = Join-Path $buildRoot $packageName
$distRoot = Join-Path $buildRoot "dist"
$workRoot = Join-Path $buildRoot "pyinstaller-work"
$specRoot = Join-Path $buildRoot "generated-specs"
$logPath = Join-Path $buildRoot "build.log"

New-Item -ItemType Directory -Path $packageRoot -Force | Out-Null
New-Item -ItemType Directory -Path (Join-Path $packageRoot "bin") -Force | Out-Null
New-Item -ItemType Directory -Path (Join-Path $packageRoot "data") -Force | Out-Null
New-Item -ItemType Directory -Path (Join-Path $packageRoot "users_us") -Force | Out-Null
Set-Content -LiteralPath (Join-Path $packageRoot "data\.keep") -Value "Runtime data is stored here." -Encoding ascii
Set-Content -LiteralPath (Join-Path $packageRoot "users_us\.keep") -Value "User data is stored here." -Encoding ascii
New-Item -ItemType Directory -Path $distRoot -Force | Out-Null
New-Item -ItemType Directory -Path $workRoot -Force | Out-Null
New-Item -ItemType Directory -Path $specRoot -Force | Out-Null

Start-Transcript -LiteralPath $logPath -Force | Out-Null
try {
    Write-Host "Version: $Version (Windows x64)"
    Write-Host "Source commit: $sourceSha"
    Write-Host "Dirty source tree: $dirty"
    Write-Host "Output: $buildRoot"

    $pythonInfo = & $python -c "import platform,sys; print(f'{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}|{platform.machine()}')"
    if ($LASTEXITCODE -ne 0 -or $pythonInfo.Trim() -ne "3.12.10|AMD64") {
        throw "需要 Python 3.12.10 x64，实际为 $pythonInfo"
    }
    & $python -m pip check
    if ($LASTEXITCODE -ne 0) { throw "当前虚拟环境依赖未通过 pip check。" }
    & $python -c "import importlib,importlib.metadata as m; importlib.import_module('clr'); importlib.import_module('webview.platforms.winforms'); importlib.import_module('webview.platforms.edgechromium'); print('PyInstaller',m.version('pyinstaller')); print('pywebview',m.version('pywebview')); print('pythonnet',m.version('pythonnet')); print('curl_cffi',m.version('curl_cffi'))"
    if ($LASTEXITCODE -ne 0) { throw "WebView2/pythonnet 或 yfinance 下载依赖预检失败。" }
    $qtInstalled = & $python -c "import importlib.util; print(bool(importlib.util.find_spec('PySide6') or importlib.util.find_spec('PyQt6') or importlib.util.find_spec('PyQt5')))"
    if ($LASTEXITCODE -ne 0 -or $qtInstalled.Trim() -ne "False") { throw "构建环境安装了 Qt；本发布包使用原生 WebView2，不应捆绑 Qt。" }

    Push-Location $repoRoot
    try {
        $mainDist = Join-Path $packageRoot "bin"
        $mainWork = Join-Path $workRoot "main"
        $mainArgs = @(
            "-m", "PyInstaller", "--noconfirm", "--clean", "--onedir", "--windowed",
            "--name", "KLineTrainer_US",
            "--paths", $repoRoot,
            "--distpath", $mainDist,
            "--workpath", $mainWork,
            "--specpath", $specRoot,
            "--contents-directory", "_internal",
            "--icon", (Join-Path $repoRoot "frontend/assets/favicon.ico"),
            "--version-file", (Join-Path $repoRoot "scripts/KLineTrainer_US.version.txt"),
            "--add-data", ((Join-Path $repoRoot "frontend") + ";frontend"),
            "--collect-all", "yfinance",
            "--collect-all", "curl_cffi",
            "--collect-data", "certifi",
            "--hidden-import", "clr",
            "--hidden-import", "clr_loader",
            "--hidden-import", "pythonnet",
            "--hidden-import", "webview.platforms.edgechromium",
            "--hidden-import", "webview.platforms.winforms",
            (Join-Path $repoRoot "webview_app/main_pywebview.py")
        )
        & $python @mainArgs
        if ($LASTEXITCODE -ne 0) { throw "PyInstaller 主程序构建失败，exit code $LASTEXITCODE" }
    }
    finally { Pop-Location }

    $mainExe = Join-Path $packageRoot "bin\KLineTrainer_US\KLineTrainer_US.exe"
    if (-not (Test-Path -LiteralPath $mainExe)) { throw "主程序 EXE 未生成：$mainExe" }
    $mainSmoke = Start-Process -FilePath $mainExe -ArgumentList "--smoke" -Wait -PassThru -WindowStyle Hidden
    if ($mainSmoke.ExitCode -ne 0) { throw "主程序无界面 smoke 检查失败，exit code $($mainSmoke.ExitCode)" }
    $mainSmokeLog = Join-Path $packageRoot "data\package-smoke-main.log"
    if (-not (Test-Path -LiteralPath $mainSmokeLog)) { throw "主程序 smoke 未留下成功结果。" }
    Get-Content -LiteralPath $mainSmokeLog | Write-Host
    Remove-Item -LiteralPath $mainSmokeLog -Force

    $assistantAvailable = $false
    $assistantDist = Join-Path $distRoot "assistant"
    $assistantWork = Join-Path $workRoot "assistant"
    try {
        Push-Location $repoRoot
        try {
            $assistantArgs = @(
                "-m", "PyInstaller", "--noconfirm", "--clean", "--onedir", "--windowed",
                "--name", "KLineTrainer_AI_Assistant",
                "--paths", $repoRoot,
                "--distpath", $assistantDist,
                "--workpath", $assistantWork,
                "--specpath", $specRoot,
                "--contents-directory", "_internal",
                "--icon", (Join-Path $repoRoot "ai_assistant.ico"),
                "--version-file", (Join-Path $repoRoot "scripts/KLineTrainer_AI_Assistant.version.txt"),
                "--add-data", ((Join-Path $repoRoot "ai_assistant.ico") + ";."),
                "--add-data", ((Join-Path $repoRoot "webview_app/ai_tester_frontend") + ";webview_app/ai_tester_frontend"),
                "--collect-data", "certifi",
                "--hidden-import", "clr",
                "--hidden-import", "clr_loader",
                "--hidden-import", "pythonnet",
                "--hidden-import", "webview.platforms.edgechromium",
                "--hidden-import", "webview.platforms.winforms",
                (Join-Path $repoRoot "ai_assistant_tester.py")
            )
            & $python @assistantArgs
            if ($LASTEXITCODE -ne 0) { throw "PyInstaller AI 助手构建失败，exit code $LASTEXITCODE" }
        }
        finally { Pop-Location }

        $assistantSource = Join-Path $assistantDist "KLineTrainer_AI_Assistant"
        $assistantExe = Join-Path $assistantSource "KLineTrainer_AI_Assistant.exe"
        if (-not (Test-Path -LiteralPath $assistantExe)) { throw "AI 助手 EXE 未生成。" }

        $assistantSmokeRoot = Join-Path $buildRoot "assistant-smoke"
        New-Item -ItemType Directory -Path (Join-Path $assistantSmokeRoot "bin") -Force | Out-Null
        New-Item -ItemType Directory -Path (Join-Path $assistantSmokeRoot "data") -Force | Out-Null
        $assistantSmokeFolder = Join-Path $assistantSmokeRoot "bin\KLineTrainer_AI_Assistant"
        Copy-Item -LiteralPath $assistantSource -Destination $assistantSmokeFolder -Recurse
        $assistantSmokeExe = Join-Path $assistantSmokeFolder "KLineTrainer_AI_Assistant.exe"
        $assistantSmoke = Start-Process -FilePath $assistantSmokeExe -ArgumentList "--smoke" -Wait -PassThru -WindowStyle Hidden
        if ($assistantSmoke.ExitCode -ne 0) { throw "AI 助手无界面 smoke 检查失败，exit code $($assistantSmoke.ExitCode)" }
        $assistantSmokeLog = Join-Path $assistantSmokeRoot "data\package-smoke-ai.log"
        if (-not (Test-Path -LiteralPath $assistantSmokeLog)) { throw "AI 助手 smoke 未留下成功结果。" }
        Get-Content -LiteralPath $assistantSmokeLog | Write-Host
        Copy-Item -LiteralPath $assistantSource -Destination (Join-Path $packageRoot "bin") -Recurse
        $assistantAvailable = $true
    }
    catch {
        Write-Warning "AI 助手构建或 smoke 未通过；主程序已通过，可作为独立选装组件：$_"
    }

    $readmeSource = Join-Path $PSScriptRoot "RELEASE_README.txt"
    Copy-Item -LiteralPath $readmeSource -Destination (Join-Path $packageRoot "README.txt")
    if ($assistantAvailable) {
        @'
@echo off
start "" "%~dp0bin\KLineTrainer_US\KLineTrainer_US.exe"
'@ | Set-Content -LiteralPath (Join-Path $packageRoot "启动美股训练器.cmd") -Encoding ascii
        @'
@echo off
start "" "%~dp0bin\KLineTrainer_AI_Assistant\KLineTrainer_AI_Assistant.exe"
'@ | Set-Content -LiteralPath (Join-Path $packageRoot "启动AI助手.cmd") -Encoding ascii
    }
    else {
        @'
@echo off
start "" "%~dp0bin\KLineTrainer_US\KLineTrainer_US.exe"
'@ | Set-Content -LiteralPath (Join-Path $packageRoot "启动美股训练器.cmd") -Encoding ascii
    }

    $metadataArgs = @(
        (Join-Path $PSScriptRoot "collect_release_metadata.py"),
        "--package-root", $packageRoot,
        "--version", $Version,
        "--source-sha", $sourceSha
    )
    if ($dirty) { $metadataArgs += "--source-dirty" }
    if ($assistantAvailable) { $metadataArgs += "--assistant-included" }
    & $python @metadataArgs
    if ($LASTEXITCODE -ne 0) { throw "发布清单或许可证收集失败。" }

    $zipPath = Join-Path $buildRoot "$packageName.zip"
    Compress-Archive -Path (Join-Path $packageRoot "*") -DestinationPath $zipPath -CompressionLevel Optimal
    $zipHash = (Get-FileHash -LiteralPath $zipPath -Algorithm SHA256).Hash.ToLowerInvariant()
    "$zipHash  $([System.IO.Path]::GetFileName($zipPath))" | Set-Content -LiteralPath "$zipPath.sha256.txt" -Encoding ascii
    Write-Host "Assistant included: $assistantAvailable"
    Write-Host "SHA256: $zipHash"
    Write-Host "ZIP: $zipPath"
}
finally {
    Stop-Transcript | Out-Null
}
