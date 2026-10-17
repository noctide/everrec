import { useCallback, useEffect, useState } from "react";
import { fetchDevices, type Device, type DevicesResponse, type Status } from "./api";

const REFRESH_MS = 30_000;

const STATUS_TEXT: Record<Status, string> = {
  ok: "正常录制",
  offline_local: "离线本地录制",
  abnormal: "没在录像",
  lost: "失联",
};

// 健康检查 ①②③④⑥（设计文档 6.1）
const CHECK_LABEL: Record<string, string> = {
  service: "服务",
  device: "摄像头",
  process: "录制进程",
  dataflow: "文件增长",
  storage: "存储",
};

const FILTERS:{ id: Status | "all"; label: string }[] = [
  { id: "all", label: "全部" },
  { id: "ok", label: "正常" },
  { id: "offline_local", label: "离线本地" },
  { id: "abnormal", label: "没在录像" },
  { id: "lost", label: "失联" },
];

function ago(sec: number): string {
  if (sec < 60) return `${sec} 秒前`;
  if (sec < 3600) return `${Math.floor(sec / 60)} 分钟前`;
  return `${Math.floor(sec / 3600)} 小时前`;
}

export default function DeviceWall() {
  const [data, setData] = useState<DevicesResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [prefix, setPrefix] = useState("");
  const [filter, setFilter] = useState<Status | "all">("all");
  const [updatedAt, setUpdatedAt] = useState<Date | null>(null);
  const [open, setOpen] = useState<Device | null>(null);

  const load = useCallback(async () => {
    try {
      setData(await fetchDevices(prefix.trim()));
      setError(null);
      setUpdatedAt(new Date());
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, [prefix]);

  useEffect(() => {
    load();
    const t = setInterval(load, REFRESH_MS);
    return () => clearInterval(t);
  }, [load]);

  const items = data?.items ?? [];
  const shown = items.filter((d) => filter === "all" || d.status === filter);
  const abnormal = items.filter((d) => d.status === "abnormal");

  return (
    <main className="page">
      <div className="page-head">
        <div>
          <h1>设备墙</h1>
          <div className="muted">
            每 30 秒自动刷新{updatedAt && ` · 最后更新 ${updatedAt.toLocaleTimeString("zh-CN", { hour12: false })}`}
          </div>
        </div>
        <label className="prefix">
          <span className="muted">位置编号</span>
          <input
            type="search"
            className="mono"
            placeholder="输入编号前缀，如 HD-PD"
            value={prefix}
            onChange={(e) => setPrefix(e.target.value)}
          />
        </label>
      </div>

      <div className="filters" role="group" aria-label="按状态筛选">
        {FILTERS.map((f) => (
          <button
            key={f.id}
            type="button"
            className={f.id === filter ? "on" : ""}
            onClick={() => setFilter(f.id)}
          >
            {f.id !== "all" && <span className={`dot ${f.id}`} />}
            {f.label} <strong>{f.id === "all" ? items.length : (data?.summary[f.id] ?? 0)}</strong>
          </button>
        ))}
      </div>

      {error && <div className="alert" role="alert"><strong>连不上服务器</strong><span>{error}</span></div>}
      {data?.mass_lost && (
        <div className="alert" role="alert">
          <strong>大批设备同时失联</strong>
          <span>多半是服务器或网络故障，而不是各台电脑本身的问题</span>
        </div>
      )}
      {abnormal.map((d) => (
        <div className="alert" role="alert" key={d.id}>
          <strong>没在录像</strong>
          <span>
            {d.code} {d.problems.join("；") || "录制已停止"}
            {d.restarts > 0 && `，已自动重启录制 ${d.restarts} 次`}
          </span>
        </div>
      ))}

      {data && items.length === 0 && (
        <p className="muted empty">还没有设备连上来。在操作区电脑上运行 everrec-agent，并把服务器地址配成本机。</p>
      )}

      <div className="grid">
        {shown.map((d) => (
          <article key={d.id} className={`card ${d.status}`} onClick={() => setOpen(d)}>
            <div className="thumb">
              <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true">
                <rect x="3" y="6" width="13" height="12" rx="2" />
                <path d="M16 10l5-3v10l-5-3" />
              </svg>
              {d.has_thumbnail && (
                <img
                  className="thumb-img"
                  src={`/api/v1/devices/${d.id}/snapshot?t=${encodeURIComponent(d.thumbnail_at ?? "")}`}
                  alt={`${d.code} 画面`}
                  loading="lazy"
                  onError={(e) => { e.currentTarget.style.display = "none"; }}
                />
              )}
              <span className="note">{d.current_segment ?? "—"}</span>
              {d.status === "lost" && d.thumbnail_at && (
                <span className="note thumb-age">最后画面 {ago((Date.now() - Date.parse(d.thumbnail_at)) / 1000)}</span>
              )}
              <span className={`pill ${d.status}`}>{STATUS_TEXT[d.status]}</span>
            </div>
            <div className="card-body">
              <div className="mono code">{d.code}</div>
              <div className="meta">
                <span>心跳 {ago(d.seconds_since_heartbeat)}</span>
                <span>待上传 {d.pending_sync}</span>
                <span>{d.hostname}</span>
              </div>
            </div>
          </article>
        ))}
      </div>

      {open && (
        <div className="drawer-backdrop" onClick={() => setOpen(null)}>
          <aside className="drawer" onClick={(e) => e.stopPropagation()} aria-label={`${open.code} 详情`}>
            <div className="drawer-head">
              <h2 className="mono">{open.code}</h2>
              <button type="button" onClick={() => setOpen(null)} aria-label="关闭">×</button>
            </div>
            <dl>
              <dt>状态</dt><dd>{STATUS_TEXT[open.status]}</dd>
              <dt>电脑</dt><dd>{open.hostname} · Agent {open.version}</dd>
              <dt>画质</dt><dd>{open.profile}</dd>
              <dt>检查</dt>
              <dd>
                {Object.entries(open.checks)
                  .map(([k, v]) => `${CHECK_LABEL[k] ?? k}${v === "ok" ? "正常" : v === "warn" ? "注意" : "异常"}`)
                  .join(" · ")}
              </dd>
              <dt>磁盘</dt><dd>{Math.round(open.disk_used * 100)}%</dd>
            </dl>
            <h3>最近日志</h3>
            {open.recent_logs.length === 0 ? (
              <p className="muted">没有警告或错误</p>
            ) : (
              <ul className="logs mono">
                {open.recent_logs.slice().reverse().map((l, i) => (
                  <li key={i}>
                    <span>{new Date(l.at).toLocaleString("zh-CN", { hour12: false })}</span>{" "}
                    <b>{l.level}</b> {l.message}
                  </li>
                ))}
              </ul>
            )}
          </aside>
        </div>
      )}
    </main>
  );
}
