import { useCallback, useEffect, useState } from "react";
import { fetchDevices, type DevicesResponse, type Status } from "./api";

// 看板大屏：只看红绿灯。绿=正常录制，黄=离线本地录制（没丢，等补传），红=没在录像或失联。
// 颜色只用在灯上，每个灯下面都带文字，不只靠颜色区分。
const REFRESH_MS = 10_000;

type Light = "green" | "yellow" | "red";

const LIGHT_OF: Record<Status, Light> = {
  ok: "green",
  offline_local: "yellow",
  abnormal: "red",
  lost: "red",
};

const LIGHT_TEXT: Record<Light, string> = {
  green: "正常",
  yellow: "离线本地录制",
  red: "需要处理",
};

const ORDER: Record<Light, number> = { red: 0, yellow: 1, green: 2 };

export default function Board() {
  const [data, setData] = useState<DevicesResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setData(await fetchDevices(""));
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, []);

  useEffect(() => {
    load();
    const t = setInterval(load, REFRESH_MS);
    return () => clearInterval(t);
  }, [load]);

  const items = (data?.items ?? [])
    .map((d) => ({ code: d.code, light: LIGHT_OF[d.status] ?? "red", why: d.problems.join("；") }))
    .sort((a, b) => ORDER[a.light] - ORDER[b.light] || a.code.localeCompare(b.code));
  const count = (l: Light) => items.filter((i) => i.light === l).length;

  return (
    <main className="board">
      <div className="board-sum">
        {(["green", "yellow", "red"] as Light[]).map((l) => (
          <div key={l} className="board-count">
            <span className={`lamp ${l}`} />
            <strong>{count(l)}</strong>
            <span>{LIGHT_TEXT[l]}</span>
          </div>
        ))}
      </div>

      {error && <div className="alert" role="alert"><strong>连不上服务器</strong><span>{error}</span></div>}
      {data?.mass_lost && (
        <div className="alert" role="alert">
          <strong>大批设备同时失联</strong>
          <span>多半是服务器或网络故障，而不是各台电脑本身的问题</span>
        </div>
      )}
      {data && items.length === 0 && <p className="muted empty">还没有设备连上来。</p>}

      <div className="board-grid">
        {items.map((i) => (
          <div key={i.code} className="board-tile" title={i.why || LIGHT_TEXT[i.light]}>
            <span className={`lamp ${i.light}`} />
            <div className="mono board-code">{i.code}</div>
            <div className={`board-text ${i.light}`}>{LIGHT_TEXT[i.light]}</div>
          </div>
        ))}
      </div>
    </main>
  );
}
