# EverRec（恒录）

面向设备操作区的轻量录像系统：每台操作区电脑 24 小时连续录制，能连上服务器就集中管理，连不上就在本地独立运行；管理网站统一查看每个摄像头是否在正常录制。

**状态：设计阶段，尚无实现代码。** 本仓库目前包含设计文档和界面设计稿。

## 核心特点

- **录像不断**：开机自启的系统服务，分段录制，断电只损失最后几秒；网络中断时录在本机，恢复后自动补传。
- **一眼看出谁没在录**：不只看进程是否在运行，还检查录像文件是否持续增长，卡住没录能在 60 秒内发现。
- **很省空间**：默认每路约 30 MB/小时，35 台 24 小时录制每天约 25 GB，默认保留 90 天并滚动覆盖。
- **不强依赖服务器**：客户端可完全本地配置运行；子公司网络单向隔离时，用中转节点由主服务器主动拉取状态。
- **普通人也能修录像**：桌面修复工具拖入文件、一键检查修复，原文件不动。

## 文档

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
docs/
  design.md            设计文档
  images/              文档中的架构图
design/
  source/              界面设计稿源文件（.dc.html）
  html/                可直接在浏览器打开的网页版
  screenshots/         界面截图
  build_preview.py     由源文件生成网页版：python design/build_preview.py
```

## 规划的技术选型

Python 3.12 + FFmpeg（采集端与修复）、FastAPI + PostgreSQL（服务端，Linux）、React + TypeScript（管理网站）、PySide6（桌面端）。详见设计文档第 12 节。
