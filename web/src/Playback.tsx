import { useMemo, useRef, useState } from "react";
import {
  fetchRecordings,
  recordingFileUrl,
  setRecordingLock,
  type Recording,
} from "./api";
import { errMsg } from "./labels";

const DAY_MS = 86_400_000;
const GAP_TOLERANCE_MS = 10_000; // 分段元数据有约 1–3 秒落盘偏移，>10 秒才算真缺口

const INTEGRITY_TEXT: Record<string, string> = {
  ok: "正常",
  repaired: "已修复",
  partial: "部分恢复",
  broken: "无法修复",
};

function today(): string {
  const d = new Date();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${d.getFullYear()}-${m}-${day}`;
}

function msOfDay(d: Date): number {
  return d.getHours() * 3_600_000 + d.getMinutes() * 60_000 + d.getSeconds() * 1000;
}

function fmtHM(d: Date): string {
  const h = String(d.getHours()).padStart(2, "0");
  const m = String(d.getMinutes()).padStart(2, "0");
  return `${h}:${m}`;
}

function fmtSpan(r: Recording): string {
  return `${fmtHM(new Date(r.start_at))} – ${fmtHM(new Date(r.end_at))}`;
}

// 分段时长不固定（默认 1 小时，也能在设置里改成 5 / 10 / 15 / 30 分钟），按数据算，不写死
function segMinutes(rs: Recording[]): number | null {
  if (rs.length === 0) return null;
  const ms = new Date(rs[0].end_at).getTime() - new Date(rs[0].start_at).getTime();
  return ms > 0 ? Math.round(ms / 60_000) : null;
}

function fmtSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

interface Bar {
  rec: Recording;
  left: number;
  width: number;
}

export default function Playback() {
  const [code, setCode] = useState("");
  const [date, setDate] = useState(today());
  const [fromTime, setFromTime] = useState("00:00");
  const [toTime, setToTime] = useState("23:59");
  const [recordings, setRecordings] = useState<Recording[]>([]);
  const [current, setCurrent] = useState<Recording | null>(null);
  const [searched, setSearched] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const videoRef = useRef<HTMLVideoElement>(null);

  const sorted = useMemo(
    () => [...recordings].sort((a, b) => a.start_at.localeCompare(b.start_at)),
    [recordings],
  );

  const bars: Bar[] = useMemo(
    () =>
      sorted.map((rec) => {
        const s = new Date(rec.start_at);
        const e = new Date(rec.end_at);
        const left = (msOfDay(s) / DAY_MS) * 100;
        const width = Math.max(((msOfDay(e) - msOfDay(s)) / DAY_MS) * 100, 0.3);
        return { rec, left, width };
      }),
    [sorted],
  );

  const gaps = useMemo(() => {
    const out: { left: number; width: number; label: string }[] = [];
    for (let i = 0; i < sorted.length - 1; i++) {
      const prevEnd = new Date(sorted[i].end_at).getTime();
      const nextStart = new Date(sorted[i + 1].start_at).getTime();
      if (nextStart - prevEnd > GAP_TOLERANCE_MS) {
        out.push({
          left: (msOfDay(new Date(sorted[i].end_at)) / DAY_MS) * 100,
          width: ((nextStart - prevEnd) / DAY_MS) * 100,
          label: `${fmtHM(new Date(sorted[i].end_at))} 起 ${Math.round((nextStart - prevEnd) / 1000)} 秒缺口`,
        });
      }
    }
    return out;
  }, [sorted]);

  const coverage = useMemo(() => {
    if (sorted.length === 0) return 0;
    const total = sorted.reduce((sum, r) => {
      const span = new Date(r.end_at).getTime() - new Date(r.start_at).getTime();
      return sum + Math.max(0, span);
    }, 0);
    return (total / DAY_MS) * 100;
  }, [sorted]);

  async function search() {
    setLoading(true);
    setError(null);
    try {
      const from = new Date(`${date}T${fromTime}:00`).toISOString();
      const to = new Date(`${date}T${toTime}:00`).toISOString();
      const rows = await fetchRecordings(code.trim(), from, to);
      setRecordings(rows);
      setCurrent(rows.length > 0 ? rows[0] : null);
      setSearched(true);
    } catch (e) {
      setError(errMsg(e));
    } finally {
      setLoading(false);
    }
  }

  function play(rec: Recording) {
    setCurrent(rec);
    // 切换 src 后从新分段开头播
    requestAnimationFrame(() => {
      if (videoRef.current) {
        videoRef.current.load();
        void videoRef.current.play();
      }
    });
  }

  function playNext() {
    if (!current) return;
    const i = sorted.findIndex((r) => r.id === current.id);
    if (i >= 0 && i + 1 < sorted.length) play(sorted[i + 1]);
  }

  async function toggleLock(rec: Recording) {
    try {
      await setRecordingLock(rec.id, !rec.locked);
      setRecordings((rows) => rows.map((r) => (r.id === rec.id ? { ...r, locked: !r.locked } : r)));
      setCurrent((c) => (c && c.id === rec.id ? { ...c, locked: !rec.locked } : c));
    } catch (e) {
      setError(errMsg(e));
    }
  }

  return (
    <main className="playback">
      <aside className="search-panel">
        <h2>检索条件</h2>
        <label>
          位置编号
          <input
            type="search"
            className="mono"
            placeholder="如 HD-PD-A-3F-014"
            value={code}
            onChange={(e) => setCode(e.target.value)}
          />
        </label>
        <label>
          日期
          <input type="date" value={date} onChange={(e) => setDate(e.target.value)} />
        </label>
        <div className="time-range">
          <label>
            从
            <input type="time" value={fromTime} onChange={(e) => setFromTime(e.target.value)} />
          </label>
          <label>
            到
            <input type="time" value={toTime} onChange={(e) => setToTime(e.target.value)} />
          </label>
        </div>
        <button type="button" className="primary" onClick={search} disabled={!code.trim() || loading}>
          {loading ? "检索中…" : "检索"}
        </button>
      </aside>

      <section className="playback-main">
        {error && (
          <div className="alert" role="alert">
            <strong>出错了</strong>
            <span>{error}</span>
          </div>
        )}

        {!searched && (
          <p className="muted empty">输入位置编号和日期，检索要回放的录像。</p>
        )}

        {searched && recordings.length === 0 && (
          <p className="muted empty">这个时间段没有录像（可能还没上传，或编号/时间不对）。</p>
        )}

        {recordings.length > 0 && (
          <>
            <div className="page-head">
              <div>
                <h1>
                  <span className="mono">{code}</span> · {date}
                </h1>
                <div className="muted">
                  当日覆盖率 <strong>{coverage.toFixed(1)}%</strong>
                  {gaps.length > 0 && <> · 缺口 {gaps.length} 处</>}
                </div>
              </div>
            </div>

            <section className="player" aria-label="播放器">
              {current && (
                <video
                  ref={videoRef}
                  controls
                  src={recordingFileUrl(current.id)}
                  onEnded={playNext}
                  style={{ width: "100%", background: "#000", aspectRatio: "16 / 9" }}
                />
              )}
              <div className="player-bar">
                <button type="button" onClick={() => current && play(current)}>
                  重播
                </button>
                <span className="mono muted">
                  {current ? `${fmtSpan(current)} · ${INTEGRITY_TEXT[current.integrity] ?? current.integrity}` : ""}
                </span>
                <div style={{ marginLeft: "auto", display: "flex", gap: 8 }}>
                  <a className="btn" href={current ? `/api/v1/recordings/${current.id}/download` : "#"}>
                    下载
                  </a>
                  <button type="button" className="btn" onClick={() => current && toggleLock(current)}>
                    {current?.locked ? "解锁" : "加锁留证"}
                  </button>
                </div>
              </div>
            </section>

            <section className="timeline" aria-label="24 小时时间轴">
              <div className="timeline-head">
                <h2>24 小时时间轴 · 点击跳转</h2>
                <div className="legend">
                  <span><i className="sw ok" />正常</span>
                  <span><i className="sw repaired" />已修复</span>
                  <span><i className="sw partial" />部分恢复</span>
                  <span><i className="sw gap" />缺口</span>
                </div>
              </div>
              <div className="timeline-track">
                {bars.map((b) => (
                  <button
                    key={b.rec.id}
                    type="button"
                    className={`seg ${b.rec.integrity}`}
                    title={`${fmtSpan(b.rec)} · ${INTEGRITY_TEXT[b.rec.integrity] ?? b.rec.integrity}`}
                    style={{ left: `${b.left}%`, width: `${b.width}%` }}
                    onClick={() => play(b.rec)}
                  />
                ))}
                {gaps.map((g, i) => (
                  <span
                    key={i}
                    className="gap"
                    title={g.label}
                    style={{ left: `${g.left}%`, width: `${g.width}%` }}
                  />
                ))}
              </div>
              <div className="ticks mono">
                {[0, 4, 8, 12, 16, 20, 24].map((h) => (
                  <span key={h} style={{ left: `${(h / 24) * 100}%` }}>
                    {String(h).padStart(2, "0")}:00
                  </span>
                ))}
              </div>
            </section>

            <section className="segments" aria-label="分段列表">
              <div className="segments-head">
                <h2>分段</h2>
                <span className="muted">
                  {segMinutes(sorted) ? `每段约 ${segMinutes(sorted)} 分钟 · ` : ""}共 {recordings.length} 段
                </span>
              </div>
              <table>
                <thead>
                  <tr>
                    <th>时间段</th>
                    <th>完整性</th>
                    <th>同步</th>
                    <th>大小</th>
                    <th>操作</th>
                  </tr>
                </thead>
                <tbody>
                  {sorted.map((r) => (
                    <tr key={r.id} className={current?.id === r.id ? "row-on" : ""}>
                      <td className="mono">{fmtSpan(r)}</td>
                      <td>
                        <span className={`badge ${r.integrity}`}>
                          {INTEGRITY_TEXT[r.integrity] ?? r.integrity}
                        </span>
                      </td>
                      <td className="muted">{r.status === "synced" ? "已同步" : r.status}</td>
                      <td className="muted">{fmtSize(r.size)}</td>
                      <td>
                        <button type="button" className="link" onClick={() => play(r)}>
                          播放
                        </button>
                        {" · "}
                        <a className="link" href={`/api/v1/recordings/${r.id}/download`}>
                          下载
                        </a>
                        {r.locked && <span className="locked"> · 已加锁</span>}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </section>
          </>
        )}
      </section>
    </main>
  );
}
