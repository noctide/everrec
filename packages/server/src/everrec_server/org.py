"""组织树：部门 - 科室 - 区域 - 属地 - 位置（前五级是树，编号是一台电脑）与责任人。

- 设备心跳带六级位置，服务端按它在树上自动建出缺的节点；设备挂在它最深的那个非空节点下。
- 责任人（责任人 + 责任部门 + 备注）可配在任意一级，下级没配就继承最近的上级；只用于显示。
- 已有设备的节点不允许改 code：code 进文件路径，改了会割裂历史录像（只能改名称和责任人）。
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from everrec_core.config import LEVEL_NAMES, LOCATION_LEVELS

from .db import OrgNode, utcnow
from .status import Status

# 状态从好到坏，汇总取最差
_BADNESS = {Status.OK: 0, Status.OFFLINE_LOCAL: 1, Status.ABNORMAL: 2, Status.LOST: 3}


def level_index(level: str) -> int:
    return LOCATION_LEVELS.index(level) + 1


def ensure_chain(s: Session, location: dict) -> int | None:
    """按设备上报的位置补齐树上缺的节点，返回设备所在（最深非空）节点的 id；没有位置返回 None。"""
    parent_id: int | None = None
    for level in LOCATION_LEVELS:
        code = str(location.get(level) or "").strip()
        if not code:
            continue
        node = s.scalar(select(OrgNode).where(
            OrgNode.parent_id.is_(parent_id) if parent_id is None else OrgNode.parent_id == parent_id,
            OrgNode.code == code,
        ))
        if node is None:
            node = OrgNode(
                parent_id=parent_id, level=level_index(level), code=code, name="", updated_at=utcnow())
            s.add(node)
            s.flush()
        parent_id = node.id
    return parent_id


def device_node_id(nodes: list[OrgNode], location: dict) -> int | None:
    """不写库地找设备所在节点（列表接口用）。找不到返回 None（归入“未归位”）。"""
    by_parent: dict[tuple[int | None, str], OrgNode] = {(n.parent_id, n.code): n for n in nodes}
    parent_id: int | None = None
    for level in LOCATION_LEVELS:
        code = str(location.get(level) or "").strip()
        if not code:
            continue
        node = by_parent.get((parent_id, code))
        if node is None:
            return None
        parent_id = node.id
    return parent_id


def resolve_owner(by_id: dict[int, OrgNode], node_id: int | None) -> dict | None:
    """从节点向上找第一个配了责任人/责任部门的节点。"""
    seen = 0
    cur = by_id.get(node_id) if node_id is not None else None
    while cur is not None and seen < 10:
        if cur.owner_name or cur.owner_dept:
            return {
                "name": cur.owner_name or "",
                "dept": cur.owner_dept or "",
                "note": cur.owner_note or "",
                "from_id": cur.id,
                "from_name": cur.name or cur.code,
                "inherited": cur.id != node_id,
            }
        cur = by_id.get(cur.parent_id) if cur.parent_id is not None else None
        seen += 1
    return None


def tree_for_agents(s: Session) -> list[dict]:
    """随心跳下发的嵌套组织树，桌面端设置里逐级联动选择用。"""
    nodes = s.scalars(select(OrgNode).order_by(OrgNode.level, OrgNode.code)).all()
    children: dict[int | None, list[OrgNode]] = {}
    for n in nodes:
        children.setdefault(n.parent_id, []).append(n)

    def build(parent_id: int | None) -> list[dict]:
        return [
            {"level": LOCATION_LEVELS[n.level - 1], "code": n.code, "name": n.name or "",
             "children": build(n.id)}
            for n in sorted(children.get(parent_id, []), key=lambda x: x.code)
        ]

    return build(None)


def flat_options(s: Session) -> dict[str, list[str]]:
    """各层级可选 code（平铺，兼容旧的下拉）。"""
    out: dict[str, list[str]] = {level: [] for level in LOCATION_LEVELS}
    for n in s.scalars(select(OrgNode).order_by(OrgNode.level, OrgNode.code)):
        lv = LOCATION_LEVELS[n.level - 1]
        if n.code not in out[lv]:
            out[lv].append(n.code)
    return out


def _summarize(statuses: list[Status]) -> dict:
    counts = {st.value: 0 for st in Status}
    for st in statuses:
        counts[st.value] += 1
    worst = max(statuses, key=lambda x: _BADNESS[x]).value if statuses else None
    return {"total": len(statuses), "counts": counts, "worst": worst}


def build_org(s: Session, device_rows: list[tuple[str, dict, Status]]) -> dict:
    """整棵树 + 每个节点（含下级）的设备汇总 + 解析后的责任人。

    device_rows：(设备编号, 设备上报的位置, 状态)。位置对不上任何节点的设备归入“未归位”。
    """
    nodes = list(s.scalars(select(OrgNode).order_by(OrgNode.level, OrgNode.code)))
    by_id = {n.id: n for n in nodes}
    under: dict[int, list[Status]] = {n.id: [] for n in nodes}
    direct: dict[int, int] = {n.id: 0 for n in nodes}
    unassigned: list[Status] = []
    for _code, location, st in device_rows:
        nid = device_node_id(nodes, location or {})
        if nid is None:
            unassigned.append(st)
            continue
        direct[nid] += 1
        cur: int | None = nid
        while cur is not None:
            under[cur].append(st)
            cur = by_id[cur].parent_id
    out = []
    for n in nodes:
        out.append({
            "id": n.id,
            "parent_id": n.parent_id,
            "level": LOCATION_LEVELS[n.level - 1],
            "level_name": LEVEL_NAMES[LOCATION_LEVELS[n.level - 1]],
            "code": n.code,
            "name": n.name or "",
            "owner_name": n.owner_name or "",
            "owner_dept": n.owner_dept or "",
            "owner_note": n.owner_note or "",
            "owner": resolve_owner(by_id, n.id),
            "direct_devices": direct[n.id],
            **_summarize(under[n.id]),
        })
    return {
        "levels": [{"key": k, "name": LEVEL_NAMES[k]} for k in (*LOCATION_LEVELS, "number")],
        "nodes": out,
        "unassigned": _summarize(unassigned),
    }
