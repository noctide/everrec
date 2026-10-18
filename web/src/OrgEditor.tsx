import { useEffect, useMemo, useState } from "react";
import { createOrgNode, deleteOrgNode, fetchOrg, updateOrgNode, type OrgNode, type OrgResponse } from "./api";
import { errMsg } from "./labels";

const LEVELS = ["dept", "section", "area", "site", "spot"];
const EMPTY = { code: "", name: "", owner_name: "", owner_dept: "", owner_note: "" };

/** 设置页“组织与责任人”：维护 部门-科室-区域-属地-位置 树，责任人可配在任意一级，下级继承。 */
export function OrgEditor({ disabled = false }: { disabled?: boolean }) {
  const [org, setOrg] = useState<OrgResponse | null>(null);
  const [selId, setSelId] = useState<number | null>(null);
  const [form, setForm] = useState(EMPTY);
  const [childCode, setChildCode] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);

  async function load() {
    try {
      setOrg(await fetchOrg());
    } catch (e) {
      setError(errMsg(e));
    }
  }
  useEffect(() => {
    load();
  }, []);

  const byParent = useMemo(() => {
    const m = new Map<number | null, OrgNode[]>();
    for (const n of org?.nodes ?? []) {
      const list = m.get(n.parent_id) ?? [];
      list.push(n);
      m.set(n.parent_id, list);
    }
    for (const list of m.values()) list.sort((a, b) => a.code.localeCompare(b.code));
    return m;
  }, [org]);

  const sel = org?.nodes.find((n) => n.id === selId) ?? null;
  useEffect(() => {
    if (sel) {
      setForm({
        code: sel.code,
        name: sel.name,
        owner_name: sel.owner_name,
        owner_dept: sel.owner_dept,
        owner_note: sel.owner_note,
      });
      setChildCode("");
    }
  }, [selId, org]); // eslint-disable-line react-hooks/exhaustive-deps

  async function run(fn: () => Promise<void>, ok?: string) {
    setError(null);
    setNote(null);
    try {
      await fn();
      await load();
      if (ok) setNote(ok);
    } catch (e) {
      setError(errMsg(e));
    }
  }

  const nextIdx = sel ? LEVELS.indexOf(sel.level) + 1 : 0;
  const canAddChild = nextIdx < LEVELS.length;
  const nextLevelName = org?.levels[nextIdx]?.name ?? "";

  function row(n: OrgNode, depth: number) {
    const owner = `${n.owner_name} ${n.owner_dept}`.trim();
    return (
      <div key={n.id}>
        <button
          type="button"
          className={`org-row ${n.id === selId ? "on" : ""}`}
          style={{ paddingLeft: 8 + depth * 18 }}
          onClick={() => setSelId(n.id)}
        >
          <span className="org-level">{n.level_name}</span>
          <span>{n.name || n.code}</span>
          {n.name && <span className="mono muted">{n.code}</span>}
          <span className="muted org-owner">{owner}</span>
          <span className="muted">{n.total} 台</span>
        </button>
        {(byParent.get(n.id) ?? []).map((c) => row(c, depth + 1))}
      </div>
    );
  }

  return (
    <section className="card-block">
      <h2>组织与责任人</h2>
      <p className="muted">
        按 部门 → 科室 → 区域 → 属地 → 位置 管理；电脑第一次连上服务器时会自动归到对应位置。
        责任人可以配在任意一级，下级没配就沿用上级。
      </p>
      {error && <div className="alert" role="alert"><strong>没成功</strong><span>{error}</span></div>}
      {note && <div className="alert" role="status"><strong>{note}</strong></div>}
      <div className="org-edit">
        <div className="org-tree-list">
          {(byParent.get(null) ?? []).map((n) => row(n, 0))}
          {org && org.nodes.length === 0 && (
            <p className="muted">还没有任何节点，可在右边新建一个部门，或等电脑连上来自动生成。</p>
          )}
          {org && org.unassigned.total > 0 && (
            <p className="muted">另有 {org.unassigned.total} 台电脑还没报上位置，升级客户端后会自动归位。</p>
          )}
        </div>
        <div className="org-detail">
          {sel ? (
            <>
              <h3>{sel.level_name}：{sel.name || sel.code}</h3>
              <div className="field-row">
                <label>编号（英文数字）
                  <input className="mono" value={form.code} disabled={sel.total > 0}
                    onChange={(e) => setForm({ ...form, code: e.target.value })} />
                </label>
                <label>显示名称
                  <input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
                </label>
              </div>
              {sel.total > 0 && (
                <p className="muted">下面已有电脑，编号会进录像路径，不能改；名称和责任人随时可改。</p>
              )}
              <div className="field-row">
                <label>责任人
                  <input value={form.owner_name} onChange={(e) => setForm({ ...form, owner_name: e.target.value })} />
                </label>
                <label>责任部门
                  <input value={form.owner_dept} onChange={(e) => setForm({ ...form, owner_dept: e.target.value })} />
                </label>
              </div>
              <div className="field">
                <label>备注（联系方式等）
                  <input value={form.owner_note} onChange={(e) => setForm({ ...form, owner_note: e.target.value })} />
                </label>
              </div>
              {sel.owner && sel.owner.inherited && !form.owner_name && !form.owner_dept && (
                <p className="muted">
                  现在沿用上级“{sel.owner.from_name}”的责任人：{sel.owner.name} {sel.owner.dept}
                </p>
              )}
              <div className="field-row">
                <button type="button" className="primary" disabled={disabled}
                  onClick={() => run(() => updateOrgNode(sel.id, form), "已保存")}>保存</button>
                <button type="button" className="btn"
                  disabled={disabled || sel.total > 0 || (byParent.get(sel.id)?.length ?? 0) > 0}
                  onClick={() => run(async () => {
                    await deleteOrgNode(sel.id);
                    setSelId(null);
                  })}>删除这个节点</button>
              </div>
              {canAddChild && (
                <div className="field-row">
                  <input className="mono" placeholder={`新建下级「${nextLevelName}」的编号`} value={childCode}
                    onChange={(e) => setChildCode(e.target.value)} />
                  <button type="button" className="btn" disabled={disabled || !childCode.trim()}
                    onClick={() => run(async () => {
                      await createOrgNode({ parent_id: sel.id, code: childCode.trim() });
                      setChildCode("");
                    }, "已新建")}>新建{nextLevelName}</button>
                </div>
              )}
            </>
          ) : (
            <>
              <p className="muted">点左边的节点来编辑；或新建一个顶层「{nextLevelName}」。</p>
              <div className="field-row">
                <input className="mono" placeholder="编号，如 FIN" value={childCode}
                  onChange={(e) => setChildCode(e.target.value)} />
                <button type="button" className="btn" disabled={disabled || !childCode.trim()}
                  onClick={() => run(async () => {
                    await createOrgNode({ code: childCode.trim() });
                    setChildCode("");
                  }, "已新建")}>新建{nextLevelName}</button>
              </div>
            </>
          )}
        </div>
      </div>
    </section>
  );
}
