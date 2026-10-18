# EverRec（恒录）

面向设备操作区的轻量录像系统：每台操作区电脑 24 小时连续录制，能连上服务器就集中管理，连不上就在本地独立运行；管理网站统一查看每个摄像头是否在正常录制。

**状态：第一版功能开发完成（WP1–WP10），尚未经 Windows 现场验收。** 录制、上传补传、保留与滚动覆盖、回放、设置与配置下发、登录与凭据、事件推送、缩略图、桌面端、打包与自动更新、中转节点均已实现并通过自动化测试；Windows 实机验证与试点验收（WP11）待做，见下方[进度](#进度)。

## 核心特点

- **录像不断**：开机自启的系统服务，分段录制，断电只损失最后几秒；网络中断时录在本机，恢复后自动补传。
- **一眼看出谁没在录**：不只看进程是否在运行，还检查录像文件是否持续增长，卡住没录能在 60 秒内发现。
- **画质好调又不会调坏**：默认 720P / 10 帧，只提供几个固定挡位，码率自动算；配置写错自动改回默认，不会停录。默认保留 90 天并滚动覆盖。
- **不强依赖服务器**：客户端可完全本地配置运行；子公司网络单向隔离时，用中转节点由主服务器主动拉取状态。
- **普通人也能修录像**：桌面修复工具拖入文件、一键检查修复，原文件不动。

## 文档

- [开发手册](AGENTS.md)：给接手开发的人和 agent，包括规矩、实测踩过的坑、Windows 验证方式。
- [剩余工作包](docs/tasks.md)：第一版剩下的工作，拆成可并行的独立任务，各有接口约定和验收标准。
- [设计文档 V0.3（精简版）](docs/design.md)：需求、架构、采集端、存储与上传、状态监控、推送与 API、视频修复、数据模型、容量规划、部署与分期计划。

## 界面设计稿

管理网站 3 页，桌面端 3 个窗口，配色为黑白灰。可交互的网页版在 [`design/html`](design/html)：下载仓库后用浏览器打开 `design/html/index.html` 即可，页面上的按钮和筛选能点击，数据均为示例。

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
  core/      everrec-core    公共核心库：配置、FFmpeg 录制命令、分段归档、健康检查、1/2 级修复、心跳格式
  agent/     everrec-agent   采集端：录制、断线照常录、启动时修复中断分段、本地滚动覆盖、心跳
  server/    everrec-server  服务端：心跳接口、失联判定、事件表、GET /api/v1/devices
web/                         管理网站（React + TypeScript + Vite）：设备墙、录像回放、设置、登录
config/everrec.example.yaml  客户端配置示例
deploy/                      WinSW 服务定义（Windows Agent）、systemd 单元（Linux 服务端）
docs/                        设计文档与架构图
design/                      界面设计稿（源文件、网页版、截图）
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

## 打包与安装（Windows）

用 PyInstaller 打 4 个程序（Agent、客户端、托盘、修复工具），Inno Setup 打安装包，GitHub Actions（`windows-latest`）自动产出。本地在 Windows 上打包：

```powershell
# 装打包工具
uv pip install pyinstaller
# 下 FFmpeg（含 libx264）到 third_party\ffmpeg\，下 WinSW 到 packaging\windows\winsw\
# 打包 4 个程序 + 放 FFmpeg 到各自 bin\
powershell -ExecutionPolicy Bypass -File packaging\windows\build.ps1 -FfmpegDir third_party\ffmpeg
# 编译安装包（iscc 随 Inno Setup 6 安装）
iscc packaging\windows\everrec.iss
```

安装包默认装 `D:\EverRec`，没有 D 盘退 `C:\EverRec` 并把本地配额调小；升级和卸载保留 `recordings`、`config`、`logs`。静默安装：`EverRecSetup-x.y.z.exe /VERYSILENT /SERVER=https://服务器 /LOCATION=012`。

**许可注意**：安装包里的 FFmpeg 带 libx264，适用 GPL。公司内部使用没有问题；若以后对外分发，需按 GPL 处理或改用其他编码器（见设计文档第 12 节）。

## 进度

| 部分 | 状态 |
| --- | --- |
| 录制、健康检查、离线自治、修复 | 完成；Windows 摄像头画质取流待实测 |
| 上传与补传、保留与滚动覆盖、回放 | 完成（自动化测试通过） |
| 设置页与配置下发、登录与凭据 | 完成 |
| 事件推送（通用 JSON + HMAC）、缩略图 | 完成 |
| 桌面端（修复工具、托盘、客户端）、打包与自动更新 | 完成；需在 Windows 上验证 |
| 子公司中转节点 | 完成 |
| 试点与验收（WP11） | 待做，需真实电脑 24 小时、断网、断电等实测 |

## 规划的技术选型

Python 3.12 + FFmpeg（采集端与修复）、FastAPI + PostgreSQL（服务端，Linux）、React + TypeScript（管理网站）、PySide6（桌面端）。详见设计文档第 12 节。
