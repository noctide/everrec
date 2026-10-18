<#
.SYNOPSIS
  EverRec 安装期系统配置（tasks.md WP9、设计文档 14.1）。

.DESCRIPTION
  由安装包以管理员运行（everrec.iss [Run] 段调用），做四件事：
    1. 允许桌面应用访问相机（否则 dshow 取不到画面）。
    2. 电源计划：接通电源时从不睡眠（24 小时连续录像的前提）。
    3. 关闭 USB 选择性暂停（防止摄像头被挂起掉线）。
    4. 配置文件 ACL：只有管理员可写（防止普通程序/用户误改服务器地址等）。
  任一步失败只记日志，不中断安装（现场机器环境各异，不能因个别项失败卡住安装）。

.PARAMETER InstallDir
  安装目录（如 D:\EverRec），用于定位 config\everrec.yaml 设 ACL。
#>
param(
    [Parameter(Mandatory = $true)]
    [string]$InstallDir
)

$ErrorActionPreference = "Continue"
$log = Join-Path $InstallDir "logs\postinstall.log"
New-Item -ItemType Directory -Force -Path (Split-Path $log) | Out-Null

function Write-Log([string]$msg) {
    $line = "[{0}] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $msg
    Add-Content -Path $log -Value $line -Encoding UTF8
    Write-Host $line
}

Write-Log "开始安装期配置，安装目录 $InstallDir"

# ---- 1. 允许桌面应用访问相机 ----
# 相机隐私开关在注册表：HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore\webcam
# Value = Allow 表示允许。桌面应用（非商店）走 webcam 的非 Packaged 分支。
try {
    $store = "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore\webcam"
    if (Test-Path $store) {
        Set-ItemProperty -Path $store -Name "Value" -Value "Allow" -ErrorAction Stop
        # 非打包（桌面）应用单独一项
        $nonPackaged = "$store\NonPackaged"
        if (Test-Path $nonPackaged) {
            Set-ItemProperty -Path $nonPackaged -Name "Value" -Value "Allow" -ErrorAction Stop
        }
        Write-Log "已允许桌面应用访问相机"
    } else {
        Write-Log "找不到相机隐私注册表项，跳过（可能系统版本不同）"
    }
} catch {
    Write-Log "设置相机权限失败（不影响安装）：$($_.Exception.Message)"
}

# ---- 2. 电源计划：接通电源从不睡眠 ----
try {
    # AC（接通电源）睡眠=0（从不），显示器可关但系统不睡
    powercfg /change standby-timeout-ac 0
    powercfg /change hibernate-timeout-ac 0
    # 关闭 USB 选择性暂停（AC）
    powercfg /setacvalueindex SCHEME_CURRENT 2a737441-1930-4402-8d77-b2bebba308a3 48e6b7a6-50f5-4782-a5d4-53bb8f07e226 0
    powercfg /setactive SCHEME_CURRENT
    Write-Log "已设电源计划：接通电源从不睡眠，已关 USB 选择性暂停"
} catch {
    Write-Log "设置电源计划失败（不影响安装）：$($_.Exception.Message)"
}

# ---- 3. 配置文件 ACL：只有管理员可写 ----
$cfg = Join-Path $InstallDir "config\everrec.yaml"
try {
    if (Test-Path $cfg) {
        # 取继承的 Administrators 组，移除其他用户的写权限（保留读）
        $acl = Get-Acl $cfg
        # 清掉继承来的规则，只留明确的
        $acl.SetAccessRuleProtection($true, $false)
        # 管理员完全控制
        $admin = New-Object System.Security.AccessControl.FileSystemAccessRule(
            "BUILTIN\Administrators", "FullControl", "Allow")
        $acl.SetAccessRule($admin)
        # SYSTEM 完全控制（服务以 SYSTEM 跑）
        $sys = New-Object System.Security.AccessControl.FileSystemAccessRule(
            "NT AUTHORITY\SYSTEM", "FullControl", "Allow")
        $acl.SetAccessRule($sys)
        Set-Acl -Path $cfg -AclObject $acl
        Write-Log "已设配置文件 ACL：仅管理员与 SYSTEM 可写"
    } else {
        Write-Log "配置文件尚不存在（首次启动会生成），跳过 ACL"
    }
} catch {
    Write-Log "设置配置文件 ACL 失败（不影响安装）：$($_.Exception.Message)"
}

Write-Log "安装期配置完成"
