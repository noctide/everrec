# 2026-10-17 审查问题修复验证

本次修复限于索引容错、状态过期、服务器地址切换、误操作保存入口、上传去重和补传阻塞。
修改保留在 `wp8-desktop` 工作区，不合并。

## 自动验证

在仓库根目录执行（PowerShell）：

```powershell
uv run pytest
uv run ruff check packages
Push-Location web
npm run build
Pop-Location
```

录像测试使用 `camera.type: test`，不打开真实摄像头。新增回归覆盖：

- 索引留下半行（包含未写完的中文字符）后，旧记录可读，新记录可追加和压缩。
- 索引暂时无法读取时，上传线程仍能启动，不阻止本地录制启动。
- 状态超过 15 秒未更新时不再报告正在录像；新状态到来后恢复显示。
- 本地模式填写服务器、切换服务器、清空服务器地址时，上传目标跟随配置更新。
- 旧服务器的上传成功回复不能删除切换后尚未送达新服务器的本地录像。
- 补传名额满或补传连接失败时，实时任务照常执行。
- 断网积压走补传，恢复连接后新归档分段走实时，不被积压队列拖住。
- 同名不同内容归档后，再次上传能正确去重；旧版普通归档记录继续支持去重。

数据库新增 `0003` 迁移，启动服务端时自动增加 `recording.source_filename`。
原始上传文件名与实际存储路径分开保存，避免冲突后追加的后缀破坏去重。
旧记录没有原始文件名时按原存储文件名兼容；此前已经产生的重名副本不自动删除，
也不凭后缀猜测原始名称，以免把不同录像混为一份。

## 需要 Leo 在 Windows 上验证

下列为尚未完成的人工界面验收，不代表摄像头或 Windows 服务已实测通过。
在仓库根目录打开 PowerShell，建立独立测试目录：

```powershell
$reviewDir = Join-Path $env:TEMP ('everrec-review-' + [guid]::NewGuid().ToString('N'))
uv run everrec-agent init $reviewDir
$reviewConfig = Join-Path $reviewDir 'config/everrec.yaml'
uv run everrec-agent set-config -c $reviewConfig camera.type=test recording.quality=saver recording.segment_seconds=10
uv run everrec-agent run -c $reviewConfig
```

另开 PowerShell，替换为上一窗口输出的测试目录，启动客户端：

```powershell
$reviewDir = '替换为刚才的测试目录'
uv run everrec-client --install-dir $reviewDir
```

预期：录制开始后显示正在录像；进入设置后没有“保存并重启录制”按钮，
画质仍为省空间档，切换设置页不会改写配置。

回到第一个窗口按 `Ctrl+C` 停止测试 Agent。等 15–20 秒后，客户端应显示
“录制服务无响应”。再运行 Agent 后应恢复显示。用下列命令打开托盘状态窗，
重复停止和恢复操作，预期相同：

```powershell
uv run everrec-tray --install-dir $reviewDir
```

本次没有改变 dshow 取流或 Windows 服务安装。真实摄像头、系统服务和 UAC 设置编辑
仍按原有工作包另行验收；设置页本次仅移除了错误入口，尚未实现完整编辑功能。
