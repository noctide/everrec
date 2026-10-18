# EverRec 开发手册（给接手的 agent）

本文件是在这个仓库里干活的规矩。动手前读完本文件、[`docs/design.md`](docs/design.md) 第 1.1 节，
以及 [`docs/tasks.md`](docs/tasks.md) 里你领到的那个工作包。

## 1. 项目一句话

EverRec（恒录）：约 35 台 Windows 操作区电脑 24 小时连续录像，能连上服务器就集中管理，连不上就在本地独立录；
Linux 服务器上的管理网站统一看每台电脑是不是在正常录像。

- 产品负责人：Leo。所有"要不要做、做成什么样"的问题问他，不要自己猜。
- 设计文档 `docs/design.md` 是需求来源；**第 1.1 节的范围表是第一版的最终依据**，标"第二期"的不做。
- 界面设计稿在 `design/`：源文件 `design/source/*.dc.html`，截图 `design/screenshots/`。配色：**冷白底 + 语义状态色**（正常 `#0B7A50` / 进行中 `#1E5FBF` / 留意 `#B45309` / 故障 `#C62828`），状态色只出现在点、徽标、浅底和文字上，不引入其他颜色；值在 `web/src/styles.css` 的 `:root` 和 `packages/desktop/src/everrec_desktop/common.py` 的 `COLORS` / `LEVEL_COLORS`，两边必须保持一致。

## 2. 设计文档之后新定的事（以这里为准）

| 事项 | 结论 |
| --- | --- |
| 推送渠道 | **不做企业微信、不做钉钉**。第一版只做通用 JSON 推送（带 HMAC 签名），接收方后定 |
| 画质 | **已定**：默认 720P / 10 帧。取代设计文档 4.4 的三档：画质 = 分辨率挡（360P / 480P / 720P / 1080P，不做 4K）+ 帧率挡（5 / 8 / 10 / 12 / 15），**码率不开放，按分辨率和帧率自动算**。定义只在 `core/profiles.py`，界面规格见 `docs/tasks.md` WP4 |
| 容量 | 设计文档第 13 节按 30 MB/小时算的容量已作废；720P / 10 帧上限约 290 MB/小时，实际占用待 Leo 实测后重算，服务器硬盘规格未定 |
| 服务端平台 | Linux（不是 Windows） |
| 实测环境 | Leo 的 Windows 笔记本（自带摄像头 "BisonCam,NB Pro"，走 dshow）；现场 USB 摄像头暂时测不了 |
| 分段时长 | **默认 1 小时一个文件**（取代设计文档的 5 分钟）。代价：最新一段录完才归档上传，回放最新画面最晚晚 1 小时；断电最多损失正在录的那一段。挡位只有 5 / 10 / 15 / 30 / 60 分钟，不让人手填秒数 |
| 屏幕录制与截屏 | 第一版做三样：**录屏**（Windows 桌面，与摄像头**二选一**）、**一键截屏**（抓当前屏幕）、**定时截屏**（本地保存，开关 + 间隔，默认关、5 分钟一张、最多 2000 张，**不上传服务器**）。屏幕源默认 **1080P / 8 帧**，屏幕的码率系数 `BITS_PER_PIXEL` 用 **0.10**（摄像头 0.07）。`recording.source` 字段先留好，第一版界面不按源筛选。抓屏只能在登录用户会话里做（服务在 Session 0 抓不到桌面），锁屏期间没画面 |

## 3. 仓库结构

```
packages/core/    everrec-core    三端共用：配置、档位、FFmpeg 命令、分段归档、健康检查、修复、心跳格式
packages/agent/   everrec-agent   采集端（Windows 服务）
packages/server/  everrec-server  服务端（FastAPI，Linux）
web/                              管理网站（React + TS + Vite）
deploy/                           WinSW 服务定义、systemd 单元
docs/  design/                    设计文档、界面设计稿
```

新组件按同样方式加包：`packages/<名字>/`，包名 `everrec-<名字>`，模块名 `everrec_<名字>`，
在根 `pyproject.toml` 的 `[tool.uv.sources]` 里登记。桌面端建议 `packages/desktop`，中转节点建议 `packages/relay`。

## 4. 跑起来与检查

```bash
uv sync                                   # Python 3.12，uv 管理
uv run pytest                             # 全部测试，约 35 秒，会用 FFmpeg 测试画面真录
uv run ruff check packages                # 必须 All checks passed
cd web && npm install && npm run build    # 必须通过 tsc 和 vite build
```

本机联调：`EVERREC_WEB_DIST=web/dist uv run everrec-server --port 8080`，
另开终端 `uv run everrec-agent init ./demo`，把 `camera.type` 改成 `test`、`server` 填 `http://127.0.0.1:8080`，
再 `uv run everrec-agent run -c ./demo/config/everrec.yaml`。没有摄像头时一律用 `camera.type: test`。

**提交前三项检查必须全过**：pytest、ruff、web build。

## 5. Git 规矩

- 每个工作包一个分支：`wp<编号>-<短名>`，例如 `wp1-upload`。做完开 PR 合到 `main`，不要直接推 `main`。
- 开工前和开 PR 前都 `git fetch origin main && git rebase origin/main`，保持 main 随时可用。
- 提交说明用中文，第一行说清做了什么；正文写原因和怎么验证的。
- 改动别人工作包"拥有"的文件时（见 `docs/tasks.md` 每个包的"主要改动"），在 PR 里写明，尽量只加不改。
- 仓库是公开的：不要提交密码、密钥、内网地址、真实录像或日志。

## 6. 代码约定

- Python 3.12，类型注解，`ruff` 规则见根 `pyproject.toml`。中文注释和中文日志（给现场的人看）。
- 配置用 pydantic 模型；Agent 与服务端之间的消息格式统一放在 `everrec_core/protocol.py`，两端共用，**不要在两边各写一份**。
- 服务端：新表必须同时引入 Alembic 迁移（第一个加表的工作包负责把 `Base.metadata.create_all` 换成 Alembic）；
  时间一律存 UTC 带时区；SQLite 取回的时间不带时区，用 `status.aware()` 处理。开发用 SQLite，正式用 PostgreSQL，两者都要能跑。
- 状态取值沿用设计文档第 11 节：`recording.status` = local / syncing / synced / failed，`integrity` = ok / repaired / partial / broken。
- 前端：样式变量在 `web/src/styles.css` 的 `:root`，照设计稿配色，不引入组件库。
- 面向现场人员的文字（日志、界面、推送）用大白话，不出现技术参数。

## 7. 不能破坏的原则

1. **录像不依赖服务器**。Agent 的主循环里不能有阻塞的网络调用（心跳在后台线程，上传也必须在后台线程）；
   服务器不可用、返回错误、超时，都只能影响上传和状态上报，不能影响录制。
2. **原文件永远不改动**。修复结果另存；坏的原文件移到 `recordings/_quarantine`，不删除。
3. **一台电脑只有一个录制进程**（`single_instance.py`）。
4. **配置变更只在分段边界生效**，不在一段录像中间切。
5. **录像目录、服务器地址、开机自启是本地设置**，服务器下发的配置不能覆盖。
6. **位置编号只用英文、数字、连字符**（进文件路径）。
7. **未同步的录像不能被本地滚动覆盖删掉**（有服务器时只删已同步的）。

## 8. 实测踩过的坑（改相关代码前必读）

这些都是在 Leo 的 Windows 电脑上真实出过的问题，每条都有测试守着，**不要"简化"掉**：

| 位置 | 坑 | 正确做法 |
| --- | --- | --- |
| `core/ffmpeg.py` | 外层 `-flush_packets 1` 对分段器无效，当前分段在切段前一直是 0 字节，"文件是否增长"检查形同虚设 | 用 `-segment_format_options flush_packets=1` 传给内层 TS 封装器 |
| `core/ffmpeg.py` | Windows 水印字体路径里的盘符冒号 | 写成 `fontfile='C\:/Windows/Fonts/arial.ttf'`（引号 + 转义，Windows 实测只有这种稳定） |
| `agent/recorder.py` | Windows 上 `Popen.terminate()` 是强杀，最后一段会坏 | 停止时往 FFmpeg 的 stdin 写 `q`，超时才 `kill()`；所以命令里不能有 `-nostdin` |
| `agent/child_guard.py` | 任务管理器结束 Agent 后 FFmpeg 残留，摄像头灯一直亮，再启动时搬不动正在写的文件（WinError 32）崩溃 | Windows 作业对象（关闭即结束）、Linux `PR_SET_PDEATHSIG`、启动时 `kill_orphans()`、恢复分段逐个 try |
| `agent/agent.py` | FFmpeg 刚重启、文件还没写出数据时被当成"在录"，状态来回跳 | `watcher.grew_since_reset` 为真才算在录；宽限期内心跳带 `starting=True`，服务端不判异常 |
| `agent/single_instance.py` | 普通用户在交互会话里可能无权创建 `Global\` 互斥锁 | 失败时退回 `Local\` |
| `agent/recorder.py` | 笔记本摄像头输出 MJPEG，每次启动都打 `deprecated pixel format used` | 已列为无害提示（`HARMLESS_HINTS`），只记调试日志，不上报 |
| dshow 设备名 | 名称可能含逗号，如 `BisonCam,NB Pro` | 原样传 `video=<名称>`，不要按逗号切分 |
| `core/ffmpeg.py` | dshow 不指定格式时摄像头按自己的默认格式出图（常见 640×480），设成 720P 也只是放大 | 先探测摄像头支持的格式（`list_camera_modes`），按 `choose_mode` 选宽高都够的最小格式并传 `-video_size`；摄像头达不到时降低录制分辨率并提示，不做放大 |
| `core/config.py` | 配置文件写错一个值，程序启动即报错，录像停了 | `load_config` 容错：写错的项回到默认值，警告写日志并随心跳上报，设备墙可见 |
| 测试 | Windows 上摄像头和 FFmpeg 启动比 Linux 慢 | 测试里不写死"第几秒开始在录"，只断言"录上了且之后不掉线" |

## 9. Windows 相关改动的验证

开发环境是 Linux，**凡是 `sys.platform == "win32"` 分支、dshow、Windows 服务、安装包、PySide6 界面在 Windows 上的表现，
你都无法自己验证**。这类改动：

1. 能在 Linux 上测的部分照常写测试（例如用 `testsrc` 代替摄像头、用 Linux 的等价机制复现同一场景）。
2. 在 PR 描述里写一节"**需要 Leo 在 Windows 上验证**"，给出逐条可复制的 **PowerShell 命令**（Leo 用 pwsh）和预期结果。
3. 不要在任何地方声称 Windows 部分"已验证"，除非 Leo 回报过结果。

Leo 的 Windows 测试目录：仓库在 `D:\05_project\08_everrec\everrec`，录制点安装目录 `D:\EverRec`。

## 10. 需要停下来问 Leo 的情况

- 范围拿不准是第一版还是第二期。
- 要改默认档位、保留天数、推送格式等会影响容量或对接方的默认值。
- 设计文档和本文件、或和代码现状矛盾。
- 需要新增第三方服务、云服务、付费组件，或引入 GPL 以外的新许可证问题。

## 11. 当前进度

第一版主线已打通：录制 → 健康检查 → 心跳 → 失联判定 → 设备墙；Windows 上已实测：录制、自动找摄像头、正常停止、FFmpeg 被结束后自动重启、Agent 被强制结束后 FFmpeg 跟着退出（摄像头灯灭）。
画质改为分辨率 + 帧率挡后的摄像头取流（`-video_size` / MJPEG）尚待 Windows 实测。
剩余工作和顺序见 [`docs/tasks.md`](docs/tasks.md)。
