import { useCallback, useEffect, useState } from "react";
import { fetchDevices, type Device, type DevicesResponse, type Status } from "./api";
import DeviceDrawer from "./DeviceDrawer";
import { ago, errMsg, STATUS_TEXT } from "./labels";

const REFRESH_MS = 30_000;

const FILTERS:{ id: Status | "all"; label: string }[] = [
  { id: "all", label: "全部" },
  { id: "ok", label: "正常" },
  { id: "offline_local", label: "离线本地" },
  { id: "abnormal", label: "没在录像" },
  { id: "lost", label: "失联" },
];

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
      setError(errMsg(e));
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
            工作台：逐台看画面、查日志、进回放 · 每 30 秒自动刷新
            {updatedAt && ` · 最后更新 ${updatedAt.toLocaleTimeString("zh-CN", { hour12: false })}`}
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

      {open && <DeviceDrawer device={open} onClose={() => setOpen(null)} onRevoked={load} />}
    </main>
  );
}
