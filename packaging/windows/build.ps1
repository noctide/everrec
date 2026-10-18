<#
.SYNOPSIS
  EverRec 打包脚本（tasks.md WP9）：PyInstaller onedir 打三个程序，FFmpeg 放进每个程序的 bin/。

.DESCRIPTION
  在 Windows 上运行（需已 uv sync 装好依赖 + pyinstaller）。
  产出 dist\everrec-agent、dist\everrec-desktop、dist\everrec-screen，
  供 Inno Setup（everrec.iss）打安装包。

  Python 用项目自己的 .venv：CI 里 `uv pip install pyinstaller` 装进的是 .venv，
  直接叫 `python` 会用到 runner 上的系统 Python，PyInstaller 根本不在里面。
  Qt 只收进桌面端：Agent 和录屏进程不 import PySide6，塞进去每个包白多一百多 MB。

  FFmpeg 从 -FfmpegDir 指定的目录取 ffmpeg.exe / ffprobe.exe（CI 先下载好），
  放进每个程序的 bin\，Agent 靠"与 exe 同级的 bin\ffmpeg.exe"定位（core/ffmpeg.py）。

.EXAMPLE
  .\build.ps1 -FfmpegDir ..\..\third_party\ffmpeg
#>
param(
    [string]$FfmpegDir = "",
    [string]$Version = "0.2.0"
)

$ErrorActionPreference = "Stop"
$root = Resolve-Path (Join-Path $PSScriptRoot "..\..")
Set-Location $root

# 三个程序：名字 -> 入口脚本 -> 是否 GUI（GUI 不带控制台窗口）-> 是否收 Qt
$apps = @(
    @{ Name = "everrec-agent";   Entry = "packaging\windows\entry\agent.py";   Gui = $false; Qt = $false },
    @{ Name = "everrec-desktop"; Entry = "packaging\windows\entry\desktop.py"; Gui = $true;  Qt = $true  },
    # 录屏进程：跑在登录用户的会话里，无窗口（tasks.md WP12）
    @{ Name = "everrec-screen";  Entry = "packaging\windows\entry\screen.py";  Gui = $true;  Qt = $false }
)

$srcDirs = @(
    "packages\core\src",
    "packages\agent\src",
    "packages\desktop\src"
)

# 用项目虚拟环境里的 Python（CI 的 pyinstaller 装在 .venv 里）
$python = if (Test-Path ".venv\Scripts\python.exe") { ".venv\Scripts\python.exe" } else { "python" }

foreach ($app in $apps) {
    Write-Host "=== 打包 $($app.Name) ===" -ForegroundColor Cyan
    $mode = if ($app.Gui) { "--windowed" } else { "--console" }
    $pyArgs = @(
        "--noconfirm",
        "--clean",
        "--onedir",
        $mode,
        "--name", $app.Name,
        "--paths", ($srcDirs -join ";"),
        "--collect-all", "everrec_core",
        "--distpath", "dist",
        "--workpath", "build\$($app.Name)",
        "--specpath", "build"
    )
    if ($app.Qt) {
        # PySide6 的 Qt 插件与平台库要整包收进来
        $pyArgs += @("--collect-all", "PySide6")
    }
    $pyArgs += $app.Entry
    & $python -m PyInstaller @pyArgs
    if ($LASTEXITCODE -ne 0) { throw "打包 $($app.Name) 失败" }

    # FFmpeg 放进 bin\（Agent、录屏进程与修复工具都要用）
    if ($FfmpegDir) {
        $bin = "dist\$($app.Name)\bin"
        New-Item -ItemType Directory -Force -Path $bin | Out-Null
        Copy-Item (Join-Path $FfmpegDir "ffmpeg.exe")  $bin -Force
        Copy-Item (Join-Path $FfmpegDir "ffprobe.exe") $bin -Force
    }
}

Write-Host "`n打包完成，产出在 dist\。版本 $Version。" -ForegroundColor Green
Write-Host "下一步：用 everrec.iss 打安装包（iscc everrec.iss）。"
