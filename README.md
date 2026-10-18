# EverRec（恒录）

面向设备操作区的轻量录像系统：每台操作区电脑 24 小时连续录制，能连上服务器就集中管理，连不上就在本地独立运行；管理网站统一查看每个摄像头是否在正常录制。

**状态：第一版功能已全部实现（WP1–WP13），当前版本 0.2.0（预览版），尚未经 Windows 现场验收。**
录制、上传补传、保留与滚动覆盖、回放、设置与配置下发、登录与凭据、事件推送、缩略图、
屏幕录制与截屏、六级组织树与责任人、桌面端、打包与自动更新、中转节点均已实现并通过自动化测试；
预览版安装包见 [Releases](https://github.com/noctide/everrec/releases)，
Windows 实机验证与试点验收（WP11）待做，见[验收清单](docs/acceptance.md)。

## 核心特点

- **录像不断**：开机自启的系统服务，分段录制，断电只损失最后几秒；网络中断时录在本机，恢复后自动补传。
- **一眼看出谁没在录**：不只看进程是否在运行，还检查录像文件是否持续增长，卡住没录能在 60 秒内发现。
- **画质好调又不会调坏**：默认 720P / 10 帧，只提供几个固定挡位，码率自动算；配置写错自动改回默认，不会停录。默认保留 90 天并滚动覆盖。
- **不强依赖服务器**：客户端可完全本地配置运行；子公司网络单向隔离时，用中转节点由主服务器主动拉取状态。
- **普通人也能修录像**：桌面修复工具拖入文件、一键检查修复，原文件不动。

## 文档

- [开发手册](AGENTS.md)：给接手开发的人和 agent，包括规矩、实测踩过的坑、Windows 验证方式。
- [工作包与验收](docs/tasks.md)：第一版各工作包的实现与验收标准（WP1–WP13 已全部实现，只剩 WP11 现场验收）。
- [现场验收清单](docs/acceptance.md)：装好之后照着做的逐项 PowerShell 步骤和记录表。
- [设计文档 V0.3（精简版）](docs/design.md)：需求、架构、采集端、存储与上传、状态监控、推送与 API、视频修复、数据模型、容量规划、部署与分期计划。

## 界面设计稿

管理网站 4 页（首页 / 设备墙 / 录像回放 / 设置），桌面端 3 个窗口，配色为黑白灰。可交互的网页版在 [`design/html`](design/html)：下载仓库后用浏览器打开 `design/html/index.html` 即可，页面上的按钮和筛选能点击，数据均为示例。

> 设计稿是改版前画的：现在的首页只放编号和状态（不放画面），画面统一去设备墙看。

### 管理网站

| 设备墙 | 录像回放 |
| --- | --- |
| ![设备墙](design/screenshots/01-device-wall.png) | ![录像回放](design/screenshots/02-playback.png) |

| 设置 |
| --- |
| ![设置](design/screenshots/03-settings.png) |

### 桌面端

| 录像客户端 | 离线时 | 设置（点左上角图标进入） |
| --- | --- | --- |
| ![录像客户端](design/screenshots/04-client.png) | ![离线](design/screenshots/05-client-offline.png) | ![客户端设置](design/screenshots/06-client-settings.png) |

| 修复工具 | 修复结果 | 托盘状态窗 |
| --- | --- | --- |
| ![修复工具](design/screenshots/07-repair-tool.png) | ![修复结果](design/screenshots/08-repair-tool-result.png) | ![托盘](design/screenshots/09-tray.png) |

## 目录结构

```
packages/
  core/      everrec-core    三端共用：配置、档位、FFmpeg 命令、分段归档、健康检查、修复、心跳格式
  agent/     everrec-agent   采集端（Windows 服务）：录制、离线照常录、自动修复、上传补传、自动更新、录屏会话进程
  server/    everrec-server  服务端（FastAPI）：心跳与失联、上传与保留、回放、登录与凭据、推送、缩略图、安装包管理
  desktop/   everrec-desktop 桌面端（PySide6）：客户端窗口、托盘、修复工具、一键 / 定时截屏
  relay/     everrec-relay   子公司中转节点：对子公司 Agent 提供同样的接口，向主服务器拉取
web/                         管理网站（React + TS + Vite）：首页、设备墙、录像回放、设置（含组织树与责任人）
packaging/windows/           PyInstaller 打包脚本 + Inno Setup 安装包脚本
deploy/                      WinSW 服务定义（Windows Agent）、systemd 单元（Linux 服务端）
docs/                        设计文档、工作包与验收清单
design/                      界面设计稿（源文件、网页版、截图）
config/everrec.example.yaml  客户端配置示例
```

## 本地跑起来

需要 Python 3.12、[uv](https://docs.astral.sh/uv/)、FFmpeg（带 libx264）、Node 20+。

```bash
uv sync                                   # 安装 Python 依赖
uv run pytest                             # 跑测试（会用 FFmpeg 测试画面真录几秒）

cd web && npm install && npm run build && cd ..
EVERREC_WEB_DIST=web/dist uv run everrec-server --port 8080   # 浏览器打开 http://127.0.0.1:8080

# 另开一个终端：起一台模拟录制点（camera.type 改成 test 就不需要真摄像头）
uv run everrec-agent init ./demo
#   编辑 ./demo/config/everrec.yaml：server 填 http://127.0.0.1:8080，camera.type 填 test
uv run everrec-agent run -c ./demo/config/everrec.yaml
```

开发管理网站时用 `cd web && npm run dev`，`/api` 会自动转发到 8080。

## 开发：打包与安装（Windows）

用 PyInstaller 打 3 个 onedir（`everrec-agent`、`everrec-desktop`、`everrec-screen`；客户端、托盘、修复工具是同一个 exe 的三个入口），Inno Setup 打安装包，GitHub Actions（`windows-latest`）在推 `v*` tag 时自动产出。本地在 Windows 上打包：

```powershell
# 装打包工具
uv pip install pyinstaller
# 下 FFmpeg（含 libx264）到 third_party\ffmpeg\，下 WinSW 到 packaging\windows\winsw\
# 打包 3 个 onedir + 放 FFmpeg 到各自 bin\
powershell -ExecutionPolicy Bypass -File packaging\windows\build.ps1 -FfmpegDir third_party\ffmpeg
# 编译安装包（iscc 随 Inno Setup 6 安装）
iscc packaging\windows\everrec.iss
```

安装包输出到仓库根的 `Output\EverRecSetup-x.y.z.exe`（CI 上传的就是这个路径）。

安装包默认装 `D:\EverRec`，没有 D 盘退 `C:\EverRec` 并把本地配额调小；升级和卸载保留 `recordings`、`config`、`logs`。静默安装：`EverRecSetup-x.y.z.exe /VERYSILENT /SERVER=https://服务器 /LOCATION=012`。

**许可注意**：安装包里的 FFmpeg 带 libx264，适用 GPL。公司内部使用没有问题；若以后对外分发，需按 GPL 处理或改用其他编码器（见设计文档第 12 节）。

## 预览版下载

当前预览版 **0.2.0** 在 GitHub 的 [Releases](https://github.com/noctide/everrec/releases) 里（标着 "Pre-release"），
下载 `EverRecSetup-0.2.0.exe` 装到现场电脑即可。预览版用于 Windows 现场验收，装之前先看
[验收清单](docs/acceptance.md)；问题按清单最后一节的方式记下来。

## 安装与使用（现场，Windows）

**要求**：Windows 10 / 11（64 位）。录像占盘按上限约 290 MB/小时估算，默认本地配额 20 GB、保留 90 天。

1. **安装**：右键"以管理员身份运行"`EverRecSetup-0.2.0.exe`（双击也行，会弹 UAC）。
   默认装到 `D:\EverRec`，没有 D 盘会装到 `C:\EverRec` 并自动把本地配额调小。
   批量安装用静默参数：`EverRecSetup-0.2.0.exe /VERYSILENT /SERVER=http://服务器:8080 /LOCATION=012`。
2. **填服务器地址和位置编号**：打开桌面端"录像客户端"→ 设置里填；装了桌面快捷方式的话直接点开。
   位置编号只能用英文、数字、连字符（会进文件路径）。
3. **确认在录**：客户端窗口显示"正在录像"、管理网站上出现这台电脑即可。日志在 `D:\EverRec\logs\agent.log`。
4. **看管理网站**：浏览器打开服务器地址，用管理员密码登录（密码由服务器端 `EVERREC_ADMIN_PASSWORD` 设定）。
   首页一屏看全部电脑状态，点设备墙看画面和日志，录像回放在"录像回放"页。
5. **卸载**：设置 → 应用，或控制面板卸载；`recordings` / `config` / `logs` 会保留。
6. **现场验收**：按[验收清单](docs/acceptance.md)逐项做，结果填第 D 节的表。

> 之前装过 0.1.0 的机器要**手动装一次**这个预览版（旧版不认识新版心跳字段），之后就能自动更新了。

## 进度

| 部分 | 状态 |
| --- | --- |
| 录制、健康检查、离线自治、修复 | 完成；Windows 摄像头画质取流待实测 |
| 上传与补传、保留与滚动覆盖、回放 | 完成（自动化测试通过） |
| 设置页与配置下发、登录与凭据 | 完成 |
| 事件推送（通用 JSON + HMAC）、缩略图 | 完成 |
| 屏幕录制（与摄像头二选一）、一键 / 定时截屏 | 完成；需在 Windows 上验证 |
| 六级组织树与责任人、首页 | 完成；首页只放编号和状态，画面在设备墙看 |
| 桌面端（修复工具、托盘、客户端）、打包与自动更新 | 完成；需在 Windows 上验证 |
| 子公司中转节点 | 完成 |
| 试点与验收（WP11） | 进行中：0.2.0 预览版已发布，等现场按验收清单逐项做 |

## 规划的技术选型

Python 3.12 + FFmpeg（采集端与修复）、FastAPI + PostgreSQL（服务端，Linux）、React + TypeScript（管理网站）、PySide6（桌面端）。详见设计文档第 12 节。
