# 第一版验收清单（WP11）

设计文档第 16 节规定，第一版先在一台电脑上连续录 24 小时，再推全面。这份清单把六条硬指标拆成
可以直接照着做的步骤，加上上线前必须补验的几项环境行为。

**谁做**：Leo 现场操作，我负责按结果修问题、重跑不通过项。
**通过标准**：A 组六条全过，B 组没有阻断级问题（录不上、丢段、补传失败、装完不能用）。
**怎么记**：做完一项就填第 D 节的表。证据（日志片段、截图、录屏）留在本机或私有位置——
仓库是公开的，别把真实录像、日志、内网地址提交上来。

## 0. 前置

- 一台操作区电脑装好 EverRec，位置编号已知（记作 `<编号>`，例如 `HD-PD-A-03-012`）
- 一台 Linux 服务器跑 `everrec-server` 和管理网站，地址记作 `<服务器>`
- 设备墙上已经能看到这台电脑（看不到就先别往下走）
- 推送接收端配一个测试地址；没配的话 A5 只看设备墙，不看推送

先确认基本状态，后面每一项都用到：

```powershell
$app = "D:\EverRec"
Get-Service | Where-Object { $_.DisplayName -like "*EverRec*" } | Format-Table Name,Status
Get-Content "$app\state\status.json" -Raw | ConvertFrom-Json |
  Select-Object code,recording,checks,pending_sync,offline_since,paused
Get-Content "$app\logs\agent.log" -Tail 30
```

**分段缺口检查脚本**（A1、A2 都要用，直接整段粘进 pwsh）：

```powershell
$app = "D:\EverRec"
$lines = Get-Content "$app\state\segments.jsonl" | ForEach-Object { $_ | ConvertFrom-Json }
# 索引是追加式写入：同一段的状态变化（local → synced）会多写一行，所以按路径去重、取最后一行
$latest = @{}
foreach ($e in $lines) { $latest[$e.path] = $e }
$segs = $latest.Values | Sort-Object { [datetime]$_.started_at }

$bad = 0; $unknown = 0
for ($i = 1; $i -lt $segs.Count; $i++) {
    $prev = $segs[$i - 1]; $cur = $segs[$i]
    if ([double]$prev.duration -le 0) { $unknown++; continue }   # 旧索引没有 duration
    $end = [datetime]$prev.started_at + [timespan]::FromSeconds([double]$prev.duration)
    $gap = ([datetime]$cur.started_at - $end).TotalSeconds
    if ($gap -gt 5) {
        $bad++
        "缺口 {0:N0} 秒：{1} → {2}" -f $gap, (Split-Path $prev.path -Leaf), (Split-Path $cur.path -Leaf)
    }
}
"共 {0} 段（去重后，原始 {1} 行），缺口 {2} 处，{3} 段没有 duration 无法判断" -f `
    $segs.Count, $lines.Count, $bad, $unknown
```

> 正常情况应该有少量 1–3 秒的间隙（切段时编码器收尾），只有 **>5 秒**才会被这条脚本报出来。
> `duration` 是后加的字段：**升级前录的段读出来是 0**，这些段会算进"无法判断"，按文件名时刻和文件大小抽查即可；
> 新录的段都带。

## A. 六条硬指标

### A1 单机 24 小时连续录制无缺口

- **操作**：记录开始时间 → 这台电脑 24 小时不要关机、不要动摄像头、不要改配置 → 记录结束时间。
- **期间**：第二天早上看一眼设备墙，全程应该是绿色；中途变红就记下时间点。
- **结束检查**：
  1. 跑上面的缺口检查脚本，期望 `缺口 0 处` 或只有 ≤5 秒的间隙；
  2. 归档文件数与索引条数对得上（转封装失败的会保留 `.ts`，所以两个后缀一起数；
     索引要去重，同一段的状态变化会多写几行）：
     ```powershell
     (Get-ChildItem "$app\recordings" -Recurse -Include *.mp4,*.ts -File | Measure-Object).Count
     ((Get-Content "$app\state\segments.jsonl" | ForEach-Object { $_ | ConvertFrom-Json }).path |
       Sort-Object -Unique).Count
     ```
  3. 抽查任意一段能播放、画面右上角有编号和时间水印（从网站回放页看）。
- **预期**：24 小时无缺口，画面正常，没有「录制进程未运行」「文件无增长」告警。

### A2 拔网线 1 小时，恢复后完整补传

- **操作**：
  ```powershell
  Get-NetAdapter | Where-Object Status -eq 'Up' | Format-Table Name,InterfaceDescription
  Disable-NetAdapter -Name "<网卡名>" -Confirm:$false   # 拔网线也可以，效果一样
  ```
  等 1 小时；期间每隔几分钟看一眼 `pending_sync`（应该逐渐涨）和 `offline_since`（应该有值）。
  然后恢复：
  ```powershell
  Enable-NetAdapter -Name "<网卡名>" -Confirm:$false
  ```
- **预期**：
  1. 断网期间**照常录制**（`recording: true`、`_spool` 里有文件在长），设备墙显示"离线本地录制"；
  2. 恢复后 `pending_sync` 逐步归零，设备墙回到绿色；
  3. 本地已同步的分段被删掉，服务器上能查到这些分段；
  4. 跑一遍缺口检查脚本，跨断网时段也没有缺口。
- **服务器侧核对**（在服务器上执行，`<起始>`/`<结束>` 用 ISO 时间）：
  ```bash
  curl -s "http://<服务器>:8080/api/v1/recordings?code=<编号>&from=<起始>&to=<结束>" | jq 'length'
  ```
  分段条数应与断网前后本地索引里这段时间的条数一致。**哈希由服务端在收完时重算比对**
  （不符会返回 422、Agent 重传），所以能查到就说明字节是对的。
- **注意**：断网 1 小时会积压约 1 段（默认 1 小时一段）；如果现场把分段改成了 5 分钟，积压约 12 段。

### A3 拔摄像头 / 杀进程 / 断电后自动恢复

三个子场景分开测，每一项都从"设备墙是绿色、`recording: true`"开始。

**A3a 拔摄像头**（录屏模式跳过这条）
```powershell
# 拔掉 USB，或用管理员 PowerShell 禁用设备：
Get-PnpDevice -Class Camera | Format-Table FriendlyName,Status,InstanceId
Get-PnpDevice -Class Camera | Disable-PnpDevice -Confirm:$false
```
- **预期**：60 秒内设备墙变红、提示「摄像头异常」；插回（或 `Enable-PnpDevice`）后 60 秒内恢复绿色，
  日志里有 `录制进程退出` / `重启录制` 的记录。

**A3b 杀进程**
```powershell
Stop-Process -Name everrec-agent -Force        # 服务应被 WinSW 在 10 秒后拉起
Get-Process ffmpeg -ErrorAction SilentlyContinue   # 残留的 FFmpeg 应跟着退出（摄像头灯灭）
```
- **预期**：Agent 被拉起后继续录；没有残留 FFmpeg；设备墙短暂离线后恢复。
- **同时验证**：`Stop-Process -Name ffmpeg -Force`（只杀 FFmpeg）→ 10 秒内自动重启录制。

**A3c 断电**
- **操作**：直接断电（长按电源或拔电源），等 10 秒再开机。
- **预期**：开机后服务自启、自动开始录；最多少最后一段（正在写的那一段）；
  上次中断的分段被修复并归档（日志里有 `发现 1 个上次中断留下的分段`），坏文件进 `recordings\_quarantine` 而不是被删。

### A4 卡住没录 60 秒内发现

模拟"进程在、文件不涨"：把 FFmpeg 挂起 90 秒再恢复。

```powershell
Add-Type -Name N -Namespace W -MemberDefinition @'
[DllImport("ntdll.dll")] public static extern int NtSuspendProcess(IntPtr h);
[DllImport("ntdll.dll")] public static extern int NtResumeProcess(IntPtr h);
'@
$p = Get-Process ffmpeg | Select-Object -First 1
[W.N]::NtSuspendProcess($p.Handle)   # 开始挂起，看表
# ……等 90 秒，观察设备墙和日志……
[W.N]::NtResumeProcess($p.Handle)    # 恢复
```
- **预期**：挂起约 30–35 秒后，Agent 记一条「录像文件 N 秒无增长」并重启录制，设备墙变红；
  60 秒内一定能看到；恢复后自动转绿。日志关键字：`重启录制`、`无增长`。

### A5 失联 2 分钟内告警

- **操作**：`Stop-Service <服务名>`（服务名以前面的 `Get-Service` 结果为准）或者拔网线。
- **预期**：
  1. **2 分钟内**设备墙变灰、显示"失联"；
  2. 配了推送的话，收到一条 `device.lost`（带签名头 `X-Event-Id` / `X-Timestamp` / `X-Signature`）；
  3. 超过 3 台同时断，只推一条"疑似服务器或网络故障"，不逐台推；
  4. 恢复后 2 分钟内变绿，并收到 `device.recovered`。
- **注意**：判定阈值是 `EVERREC_LOST_AFTER`（默认 120 秒），服务器每 15 秒扫一次。

### A6 子公司设备状态在主公司设备墙可见

**现场真机**：子公司那台 Agent 的 `server` 填中转节点地址；主服务器配
`EVERREC_RELAYS="sub1=http://<中转节点>:8081"` 和 `EVERREC_RELAY_PULL_TOKEN`（两边一致）。
**没有子公司环境时用本机模拟**：

```bash
# 终端 1：主服务器（带上中转节点配置）
EVERREC_RELAYS="sub1=http://127.0.0.1:8081" EVERREC_RELAY_PULL_TOKEN=relay-secret \
  uv run everrec-server --port 8080
# 终端 2：中转节点
EVERREC_RELAY_PULL_TOKEN=relay-secret EVERREC_DATABASE_URL=sqlite:///./relay.db \
EVERREC_RECORDINGS_DIR=./relay-recordings uv run everrec-relay --port 8081
# 终端 3、4：两个 Agent（camera.type: test），一个 server 填 8080，一个填 8081
```
- **预期**：
  1. 设备墙上两台都在，中转节点那台能看状态、能回放（主服务器按需向中转节点取分段）；
  2. 停掉中转节点：只出一条"子公司中转节点失联"的告警，**不**把它下面每台设备都报失联；
  3. 中转节点恢复后，状态自动回到正常。

## B. 上线前补充项

| 编号 | 项 | 怎么做 | 预期 |
| --- | --- | --- | --- |
| B1 | 干净机器安装 | 在没有 EverRec 的 Windows 上跑安装包；重启一次；再卸载 | 重启后自动开录（服务 + 托盘 + 录屏进程）；卸载后 `recordings`/`config`/`logs` 还在。**先确认 `deploy/windows/everrec-agent.xml` 的 exe 路径**：现在写的是 `%BASE%\everrec-agent.exe`，安装包实际放在 `{app}\everrec-agent\`，服务可能起不来 |
| B2 | 磁盘阈值 | 服务器磁盘灌到 85% / 95% | 85% 产生 `storage.low` 事件；95% 上传接口返回 507，Agent 转本地继续录、不中断 |
| B3 | 录屏模式 | 客户端设置 → 录制画面选"屏幕"；Win+L 锁屏 10 秒再解锁；换另一个用户登录 | 5 秒内开始录；锁屏期间设备墙显示"锁屏暂停"且是绿色；解锁自动恢复；第二个登录会话不重复录 |
| B4 | 修得动 / 修不动 | 修复工具分别拖入正常文件、拔电源产生的中断文件、随便一个非视频文件 | 三种给不同结论；原文件不动，修复结果存"已修复"文件夹 |
| B5 | 自动更新 | 网站上传一个版本号更高的安装包；等 02:00–04:00 | 到点自动检查并静默安装；"只提醒"模式下只提示不装；失败回到旧版 |

## C. 已知偏差（别当 bug 报）

- **分段默认 1 小时**：最新一段要录完才上传，所以回放最新画面最多晚 1 小时。想更快就把服务器设置改成 5/10/15/30 分钟。
- **分段开始时间比画面实际开始晚约 1 秒**（编码器先攒几帧），时间轴上会有 1–3 秒的间隙，属正常。
- **锁屏期间录屏没画面**：会显示"锁屏暂停"，绿色、不推送。
- **摄像头被服务独占**：用户开会话时用不了这个摄像头。
- **设备令牌吊销后**：Agent 照常本地录制，只是上报失败，设备墙上会失联。

## D. 记录表

| 编号 | 通过? | 实际结果 | 证据（本机路径/截图名） | 备注 |
| --- | --- | --- | --- | --- |
| A1 24 小时 | | | | |
| A2 断网补传 | | | | |
| A3a 拔摄像头 | | | | |
| A3b 杀进程 | | | | |
| A3c 断电 | | | | |
| A4 卡住发现 | | | | |
| A5 失联告警 | | | | |
| A6 子公司可见 | | | | |
| B1 安装卸载 | | | | |
| B2 磁盘阈值 | | | | |
| B3 录屏模式 | | | | |
| B4 修复工具 | | | | |
| B5 自动更新 | | | | |

## E. 不通过怎么办

1. 记下编号 + 当时时间 + 复现步骤，从 `D:\EverRec\logs\agent.log` 和服务器日志各截一段（脱敏后再贴）。
2. 阻断级（录不上、丢段、补传失败、装完不能用）→ 修完重跑这一项，其余项可以继续。
3. 非阻断级（界面文字、提示语气）→ 记录进 issue，不挡上线。

## F. 结论

- 软件版本：`curl -s http://<服务器>:8080/healthz`，客户端"关于"里的版本
- 验收日期 / 参与人：
- 结论：通过 / 有条件通过（列出条件） / 不通过
