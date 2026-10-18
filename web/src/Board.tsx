import { useCallback, useEffect, useMemo, useState, type ReactElement } from "react";
import {
  fetchDevices,
  fetchOrg,
  type Device,
  type DevicesResponse,
  type OrgNode,
  type OrgResponse,
  type Owner,
  type Status,
  type Worst,
} from "./api";
import DeviceDrawer from "./DeviceDrawer";
import { ago, errMsg } from "./labels";

// 看板（首页）：左侧组织树 + 状态条 + 等大格子监控墙。
// 1. 一屏看全，不滚动；格子顺序固定按编号，刷新不跳位（格子等于物理位置）。
// 2. 状态靠 8px 点、徽标和 1px 边框区分，颜色只出现在这上面。
// 3. 点任意一格打开详情抽屉，要逐台查画面和日志去设备墙。
const REFRESH_MS = 10_000;
const UNASSIGNED = -1; // “未归位”：旧版客户端还没报上位置的电脑

type Filter = "all" | "ok" | "offline_local" | "problems";

// 格子上左上角那块小徽标：正常不显示，出问题才标
const FLAG: Record<Status, string> = {
  ok: "",
  offline_local: "离线本地录制",
  abnormal: "需要处理",
  lost: "失联",
};

/** 用大白话写这台设备现在怎么了，长度控制在格子里能放下一行。 */
function shortWhy(d: Device): string {
  if (d.status === "lost") return `${ago(d.seconds_since_heartbeat).replace("前", "")}没有消息`;
  if (d.status === "offline_local") {
    return d.pending_sync > 0 ? `连不上服务器 · 待补传 ${d.pending_sync} 段` : "连不上服务器，本机在录";
  }
  if (d.status === "abnormal") return d.problems[0] ?? "没有在录像";
  const label = d.location?.label || d.location?.number || "";
  const when = ago(d.seconds_since_heartbeat);
  return label ? `${label} · ${when}` : when;
}

function clock(d: Date): string {
  return d.toLocaleTimeString("zh-CN", { hour12: false });
}

function shotTime(at: string | null): string {
  if (!at) return "";
  const t = new Date(at);
  return Number.isNaN(t.getTime()) ? "" : t.toLocaleTimeString("zh-CN", { hour12: false });
}

const label = (n: OrgNode) => n.name || n.code;

function ownerText(o: Owner | null): string {
  if (!o) return "";
  return [o.name, o.dept].filter(Boolean).join(" · ");
}

// 选中的节点放在地址里（#/?node=3），刷新、分享链接都不丢
function nodeFromHash(): number | null {
  const q = window.location.hash.split("?")[1];
  const v = q ? new URLSearchParams(q).get("node") : null;
  return v === null || v === "" || Number.isNaN(Number(v)) ? null : Number(v);
}

function setNodeHash(id: number | null) {
  window.location.hash = id === null ? "#/" : `#/?node=${id}`;
}

const LIGHT_OF: Record<Status, string> = {
  ok: "ok",
  offline_local: "offline_local",
  abnormal: "abnormal",
  lost: "lost",
};

function worstLight(w: Worst): string {
  return w ? LIGHT_OF[w] : "";
}

export default function Board() {
  const [data, setData] = useState<DevicesResponse | null>(null);
  const [org, setOrg] = useState<OrgResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [updated, setUpdated] = useState<Date | null>(null);
  const [selected, setSelected] = useState<number | null>(nodeFromHash());
  const [open, setOpen] = useState<Set<number>>(new Set());
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState<Filter>("all");
  const [drawer, setDrawer] = useState(false);
  const [detail, setDetail] = useState<Device | null>(null);

  const load = useCallback(async () => {
    try {
      const [d, o] = await Promise.all([fetchDevices(""), fetchOrg()]);
      setData(d);
      setOrg(o);
      setUpdated(new Date());
      setError(null);
    } catch (e) {
      setError(errMsg(e));
    }
  }, []);

  useEffect(() => {
    load();
    const t = setInterval(load, REFRESH_MS);
    return () => clearInterval(t);
  }, [load]);

  useEffect(() => {
    const onHash = () => setSelected(nodeFromHash());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  const nodes = org?.nodes ?? [];
  const byId = useMemo(() => new Map(nodes.map((n) => [n.id, n])), [nodes]);
  const children = useMemo(() => {
    const m = new Map<number | null, OrgNode[]>();
    for (const n of nodes) {
      const l = m.get(n.parent_id) ?? [];
      l.push(n);
      m.set(n.parent_id, l);
    }
    for (const l of m.values()) l.sort((a, b) => a.code.localeCompare(b.code));
    return m;
  }, [nodes]);

  // 选中节点及其所有下级
  const scope = useMemo(() => {
    if (selected === null || selected === UNASSIGNED) return null;
    const ids = new Set<number>();
    const walk = (id: number) => {
      ids.add(id);
      for (const c of children.get(id) ?? []) walk(c.id);
    };
    if (byId.has(selected)) walk(selected);
    return ids;
  }, [selected, children, byId]);

  const sel = selected !== null && selected !== UNASSIGNED ? (byId.get(selected) ?? null) : null;

  // 有问题的分支默认展开；选中节点的祖先也展开
  const forced = useMemo(() => {
    const s = new Set<number>();
    for (const n of nodes) {
      if (n.counts.abnormal + n.counts.lost > 0) {
        let p = n.parent_id;
        while (p !== null) {
          s.add(p);
          p = byId.get(p)?.parent_id ?? null;
        }
      }
    }
    let p = sel ? sel.parent_id : null;
    while (p !== null) {
      s.add(p);
      p = byId.get(p)?.parent_id ?? null;
    }
    return s;
  }, [nodes, byId, sel]);

  const items = data?.items ?? [];
  const inScope = (d: Device) => {
    if (selected === null) return true;
    if (selected === UNASSIGNED) return d.node_id === null || !byId.has(d.node_id);
    return d.node_id !== null && !!scope?.has(d.node_id);
  };
  // 固定按编号排：格子等于物理位置，两次抬头还在原处
  const all = items.filter(inScope).sort((a, b) => a.code.localeCompare(b.code));
  const red = all.filter((d) => d.status === "abnormal" || d.status === "lost");
  const yellow = all.filter((d) => d.status === "offline_local");
  const green = all.filter((d) => d.status === "ok");
  const total = all.length;

  const shown = all.filter((d) =>
    filter === "all" ? true
      : filter === "problems" ? d.status === "abnormal" || d.status === "lost"
        : d.status === filter,
  );

  const overall: Status | null = total === 0 ? null : red.length > 0 ? "abnormal" : yellow.length > 0 ? "offline_local" : "ok";
  const scopeName = selected === UNASSIGNED ? "未归位" : sel ? label(sel) : "全部";

  // 面包屑
  const crumbs: OrgNode[] = [];
  for (let c = sel; c; c = c.parent_id !== null ? (byId.get(c.parent_id) ?? null) : null) crumbs.unshift(c);

  const owner = sel?.owner ?? null;
  const q = query.trim().toLowerCase();
  const matches = (n: OrgNode) => !q || label(n).toLowerCase().includes(q) || n.code.toLowerCase().includes(q);
  // 搜索时保留命中节点及其祖先、后代
  const visible = useMemo(() => {
    if (!q) return null;
    const s = new Set<number>();
    for (const n of nodes) {
      if (!matches(n)) continue;
      s.add(n.id);
      for (let p = n.parent_id; p !== null; p = byId.get(p)?.parent_id ?? null) s.add(p);
      const walk = (id: number) => {
        for (const c of children.get(id) ?? []) {
          s.add(c.id);
          walk(c.id);
        }
      };
      walk(n.id);
    }
    return s;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [q, nodes]);

  function treeRow(n: OrgNode, depth: number): ReactElement | null {
    if (visible && !visible.has(n.id)) return null;
    const kids = children.get(n.id) ?? [];
    const expanded = visible ? true : open.has(n.id) || forced.has(n.id);
    const bad = n.counts.abnormal + n.counts.lost;
    return (
      <li key={n.id}>
        <div className={`tree-row ${n.id === selected ? "on" : ""}`} style={{ paddingLeft: 6 + depth * 16 }}>
          <button
            type="button"
            className="tree-twist"
            aria-label={expanded ? "收起" : "展开"}
            disabled={kids.length === 0}
            onClick={() => {
              const s = new Set(open);
              if (s.has(n.id) || forced.has(n.id)) s.delete(n.id);
              else s.add(n.id);
              setOpen(s);
            }}
          >
            {kids.length === 0 ? "" : expanded ? "▾" : "▸"}
          </button>
          <button type="button" className="tree-name" onClick={() => choose(n.id)}>
            <span className={`dot ${worstLight(n.worst)}`} />
            <span className="tree-label">{label(n)}</span>
            <span className={`tree-count ${bad > 0 ? "alert" : ""}`}>
              {bad > 0 ? `${bad}/${n.total}` : n.total}
            </span>
          </button>
        </div>
        {expanded && kids.length > 0 && <ul>{kids.map((c) => treeRow(c, depth + 1))}</ul>}
      </li>
    );
  }

  const roots = children.get(null) ?? [];
  const un = org?.unassigned;

  function choose(id: number | null) {
    setNodeHash(id);
    setDrawer(false);
  }

  function toggleFull() {
    if (document.fullscreenElement) void document.exitFullscreen();
    else void document.documentElement.requestFullscreen();
  }

  return (
    <div className="board-layout">
      <aside className={`rail ${drawer ? "open" : ""}`}>
        <input
          className="rail-search"
          type="search"
          placeholder="搜索部门、位置、编号…"
          aria-label="搜索组织"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
        <ul className="tree">
          <li>
            <div className={`tree-row ${selected === null ? "on" : ""}`}>
              <span className="tree-twist" />
              <button type="button" className="tree-name" onClick={() => choose(null)}>
                <span className={`dot ${overall ? LIGHT_OF[overall] : ""}`} />
                <span className="tree-label">全部</span>
                <span className={`tree-count ${red.length > 0 ? "alert" : ""}`}>
                  {red.length > 0 ? `${red.length}/${items.length}` : items.length}
                </span>
              </button>
            </div>
          </li>
          {roots.map((n) => treeRow(n, 0))}
          {un && un.total > 0 && (
            <li>
              <div className={`tree-row ${selected === UNASSIGNED ? "on" : ""}`}>
                <span className="tree-twist" />
                <button type="button" className="tree-name" onClick={() => choose(UNASSIGNED)}>
                  <span className={`dot ${worstLight(un.worst)}`} />
                  <span className="tree-label">未归位</span>
                  <span className="tree-count">{un.total}</span>
                </button>
              </div>
            </li>
          )}
        </ul>
      </aside>
      {drawer && <div className="board-scrim open" onClick={() => setDrawer(false)} />}

      <main className="board-main">
        <div className="board-head">
          <button type="button" className="btn board-menu" onClick={() => setDrawer(true)}>组织</button>
          <nav className="board-crumbs" aria-label="当前位置">
            <button type="button" className="link" onClick={() => choose(null)}>全部</button>
            {selected === UNASSIGNED && <><span className="sep">/</span><strong>未归位</strong></>}
            {crumbs.map((c) => (
              <span key={c.id} style={{ display: "contents" }}>
                <span className="sep">/</span>
                {c.id === selected
                  ? <strong>{label(c)}</strong>
                  : <button type="button" className="link" onClick={() => choose(c.id)}>{label(c)}</button>}
              </span>
            ))}
            <span className="board-role">一眼看出哪些电脑没在正常录像</span>
          </nav>
          <div className="board-head-right">
            {sel && (
              <a
                className={`owner-chip ${owner ? "" : "empty"}`}
                href="#/settings"
                title={owner?.note || "到设置页的「组织与责任人」里配置"}
              >
                <span className="owner-k">管辖</span>
                {owner ? (
                  <>
                    <strong>{ownerText(owner)}</strong>
                    {owner.inherited && <span className="owner-from">继承自 {owner.from_name}</span>}
                  </>
                ) : (
                  <span>未指定责任人</span>
                )}
              </a>
            )}
            <a className="link" href="#/wall">设备墙 · 逐台查画面与日志 →</a>
          </div>
        </div>

        <div className="strip">
          <button type="button" className={`seg ${filter === "all" ? "on" : ""}`} onClick={() => setFilter("all")}>
            全部<span className="n">{total}</span>
          </button>
          <button type="button" className={`seg ${filter === "ok" ? "on" : ""}`} onClick={() => setFilter("ok")}>
            <span className="dot ok" />正常<span className="n">{green.length}</span>
          </button>
          <button type="button" className={`seg ${filter === "offline_local" ? "on" : ""}`} onClick={() => setFilter("offline_local")}>
            <span className="dot offline_local" />离线本地<span className="n">{yellow.length}</span>
          </button>
          <button type="button" className={`seg ${filter === "problems" ? "on" : ""}`} onClick={() => setFilter("problems")}>
            <span className="dot abnormal" />需要处理<span className="n">{red.length}</span>
          </button>
          <div className="strip-right">
            <span>{updated ? `${clock(updated)} 更新 · 每 ${REFRESH_MS / 1000} 秒` : "加载中…"}</span>
            <button type="button" className="btn" onClick={toggleFull}>全屏</button>
          </div>
        </div>

        {error && (
          <div className="alert" role="alert">
            <strong>连不上服务器</strong>
            <span>{error}</span>
          </div>
        )}
        {data?.mass_lost && (
          <div className="alert" role="alert">
            <strong>大批设备同时失联</strong>
            <span>多半是服务器或网络故障，而不是各台电脑本身的问题</span>
          </div>
        )}

        {total === 0 ? (
          <p className="muted empty">
            {selected === null ? "还没有设备连上来。" : `${scopeName} 下还没有设备。`}
            设备装好并连上服务器后会出现在这里。
          </p>
        ) : (
          <div className="wall">
            {shown.map((d) => (
              <button
                key={d.id}
                type="button"
                className={`cell ${d.status}`}
                onClick={() => setDetail(d)}
                title={`${d.code}  ${shortWhy(d)}`}
              >
                <span className="frame">
                  {d.has_thumbnail ? (
                    <img
                      src={`/api/v1/devices/${d.id}/snapshot?t=${encodeURIComponent(d.thumbnail_at ?? "")}`}
                      alt={`${d.code} 最后画面`}
                      loading="lazy"
                      onError={(e) => { e.currentTarget.style.display = "none"; }}
                    />
                  ) : (
                    <span className="noimg">暂无画面</span>
                  )}
                  {FLAG[d.status] && <span className={`flag ${d.status}`}>{FLAG[d.status]}</span>}
                  {d.thumbnail_at && <span className="osd">{shotTime(d.thumbnail_at)}</span>}
                </span>
                <span className="cell-foot">
                  <span className="cell-code">{d.code}</span>
                  <span className={`cell-meta ${d.status}`}>
                    <span className={`dot ${d.status}`} />
                    <span>{shortWhy(d)}</span>
                  </span>
                </span>
              </button>
            ))}
          </div>
        )}

        {shown.length === 0 && total > 0 && (
          <p className="muted empty">这个筛选下没有设备。</p>
        )}
      </main>

      {detail && (
        <DeviceDrawer device={detail} onClose={() => setDetail(null)} onRevoked={load} />
      )}
    </div>
  );
}
