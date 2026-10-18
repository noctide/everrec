<#
.SYNOPSIS
  EverRec 打包脚本（tasks.md WP9）：PyInstaller onedir 打 2 个程序，FFmpeg 放进每个程序的 bin/。

.DESCRIPTION
  在 Windows 上运行（需已 uv sync 装好依赖 + pyinstaller）。
  产出 dist\everrec-agent、dist\everrec-desktop，
  供 Inno Setup（everrec.iss）打安装包。

  FFmpeg 从 -FfmpegDir 指定的目录取 ffmpeg.exe / ffprobe.exe（CI 先下载好），
  放进每个程序的 bin\，Agent 靠"与 exe 同级的 bin\ffmpeg.exe"定位（core/ffmpeg.py）。

.EXAMPLE
  .\build.ps1 -FfmpegDir ..\..\third_party\ffmpeg
#>
param(
    [string]$FfmpegDir = "",
    [string]$Version = "0.1.0"
)

$ErrorActionPreference = "Stop"
$root = Resolve-Path (Join-Path $PSScriptRoot "..\..")
Set-Location $root

# 两个程序：名字 -> 入口脚本 -> 是否 GUI（GUI 不带控制台窗口）
$apps = @(
    @{ Name = "everrec-agent";   Entry = "packaging\windows\entry\agent.py";   Gui = $false },
    @{ Name = "everrec-desktop"; Entry = "packaging\windows\entry\desktop.py"; Gui = $true  }
)

$srcDirs = @(
    "packages\core\src",
    "packages\agent\src",
    "packages\desktop\src"
)

foreach ($app in $apps) {
    Write-Host "=== 打包 $($app.Name) ===" -ForegroundColor Cyan
    $mode = if ($app.Gui) { "--windowed" } else { "--console" }
    $args = @(
        "--noconfirm",
        "--clean",
        "--onedir",
        $mode,
        "--name", $app.Name,
        "--paths", ($srcDirs -join ";"),
        # PySide6 的 Qt 插件与平台库要整包收进来
        "--collect-all", "PySide6",
        "--collect-all", "everrec_core",
        "--distpath", "dist",
        "--workpath", "build\$($app.Name)",
        "--specpath", "build",
        $app.Entry
    )
    & python -m PyInstaller @args
    if ($LASTEXITCODE -ne 0) { throw "打包 $($app.Name) 失败" }

    # FFmpeg 放进 bin\（Agent 与修复工具都要用）
    if ($FfmpegDir) {
        $bin = "dist\$($app.Name)\bin"
        New-Item -ItemType Directory -Force -Path $bin | Out-Null
        Copy-Item (Join-Path $FfmpegDir "ffmpeg.exe")  $bin -Force
        Copy-Item (Join-Path $FfmpegDir "ffprobe.exe") $bin -Force
    }
}

Write-Host "`n打包完成，产出在 dist\。版本 $Version。" -ForegroundColor Green
Write-Host "下一步：用 everrec.iss 打安装包（iscc everrec.iss）。"
