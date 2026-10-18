"""组织树与责任人：心跳自动建节点、汇总、责任人继承、改 code 受限。"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi.testclient import TestClient

from everrec_core.protocol import Checks, Heartbeat
from everrec_server.main import create_app
from everrec_server.settings import Settings


def _loc(**kw) -> dict:
    base = {"dept": "FIN", "section": "AP", "area": "E", "site": "HQ", "spot": "F3", "number": "012"}
    base.update(kw)
    return base


def _code(loc: dict) -> str:
    return "-".join(loc[k] for k in ("dept", "section", "area", "site", "spot", "number") if loc.get(k))


def _hb(loc: dict, recording: bool = True) -> dict:
    return Heartbeat(
        code=_code(loc), location=loc, hostname="PC", agent_version="0.1.0", sent_at=datetime.now(UTC),
        recording=recording, profile="720P / 10 帧", checks=Checks(),
    ).model_dump(mode="json")


def _app(tmp_path):
    return create_app(Settings(
        database_url=f"sqlite:///{tmp_path}/t.db", recordings_dir=tmp_path / "recordings",
        sweep_seconds=3600, retention_seconds=3600, admin_password="test", secret_key="test-secret",
    ))


def _login(c: TestClient) -> None:
    assert c.post("/api/v1/login", json={"password": "test"}).status_code == 200


def _node(org: dict, code: str) -> dict:
    return next(n for n in org["nodes"] if n["code"] == code)


def test_heartbeat_builds_tree_and_rolls_up_status(tmp_path):
    with TestClient(_app(tmp_path)) as c:
        _login(c)
        c.post("/agent/v1/heartbeat", json=_hb(_loc(number="012")))
        c.post("/agent/v1/heartbeat", json=_hb(_loc(number="013"), recording=False))  # 没在录像 = 红
        c.post("/agent/v1/heartbeat", json=_hb(_loc(section="HR", number="001")))
        org = c.get("/api/v1/org").json()
        assert [n["code"] for n in org["nodes"] if n["level"] == "dept"] == ["FIN"]
        fin = _node(org, "FIN")
        assert fin["total"] == 3 and fin["worst"] == "abnormal"
        assert _node(org, "AP")["total"] == 2 and _node(org, "HR")["worst"] == "ok"
        assert org["unassigned"]["total"] == 0


def test_old_agent_without_location_is_unassigned(tmp_path):
    with TestClient(_app(tmp_path)) as c:
        _login(c)
        c.post("/agent/v1/heartbeat", json=Heartbeat(
            code="HD-A-1", hostname="PC", agent_version="0.1.0", sent_at=datetime.now(UTC),
            recording=True, profile="720P / 10 帧", checks=Checks()).model_dump(mode="json"))
        org = c.get("/api/v1/org").json()
        assert org["nodes"] == [] and org["unassigned"]["total"] == 1
        dev = c.get("/api/v1/devices").json()["items"][0]
        assert dev["node_id"] is None and dev["owner"] is None


def test_owner_is_inherited_and_shown_on_devices(tmp_path):
    with TestClient(_app(tmp_path)) as c:
        _login(c)
        c.post("/agent/v1/heartbeat", json=_hb(_loc()))
        org = c.get("/api/v1/org").json()
        ap = _node(org, "AP")["id"]
        r = c.put(f"/api/v1/org/nodes/{ap}", json={"owner_name": "张三", "owner_dept": "一科"})
        assert r.status_code == 200, r.text
        org = c.get("/api/v1/org").json()
        assert _node(org, "F3")["owner"]["name"] == "张三" and _node(org, "F3")["owner"]["inherited"] is True
        assert _node(org, "AP")["owner"]["inherited"] is False
        assert _node(org, "FIN")["owner"] is None
        # 下级自己配了就用自己的
        c.put(f"/api/v1/org/nodes/{_node(org, 'F3')['id']}", json={"owner_name": "李四"})
        dev = c.get("/api/v1/devices").json()["items"][0]
        assert dev["owner"]["name"] == "李四" and dev["owner"]["from_name"] == "F3"


def test_node_with_devices_cannot_change_code_or_be_deleted(tmp_path):
    with TestClient(_app(tmp_path)) as c:
        _login(c)
        c.post("/agent/v1/heartbeat", json=_hb(_loc()))
        org = c.get("/api/v1/org").json()
        fin = _node(org, "FIN")
        assert c.put(f"/api/v1/org/nodes/{fin['id']}", json={"code": "FIN2"}).status_code == 409
        assert c.put(f"/api/v1/org/nodes/{fin['id']}", json={"name": "财务部"}).status_code == 200
        assert c.delete(f"/api/v1/org/nodes/{_node(org, 'F3')['id']}").status_code == 409


def test_create_and_delete_empty_node_and_tree_reaches_agents(tmp_path):
    with TestClient(_app(tmp_path)) as c:
        _login(c)
        r = c.post("/api/v1/org/nodes", json={"code": "OPS", "name": "运维部"})
        assert r.status_code == 200
        child = c.post("/api/v1/org/nodes", json={"parent_id": r.json()["id"], "code": "NOC"})
        assert child.status_code == 200
        dup = c.post("/api/v1/org/nodes", json={"parent_id": r.json()["id"], "code": "NOC"})
        assert dup.status_code == 409
        assert c.post("/api/v1/org/nodes", json={"code": "运维"}).status_code == 422
        reply = c.post("/agent/v1/heartbeat", json=_hb(_loc())).json()
        tree = {n["code"]: n for n in reply["location_tree"]}
        assert tree["OPS"]["name"] == "运维部" and tree["OPS"]["children"][0]["code"] == "NOC"
        assert reply["location_options"]["dept"] == ["FIN", "OPS"]
        assert c.delete(f"/api/v1/org/nodes/{r.json()['id']}").status_code == 409  # 还有下级
        assert c.delete(f"/api/v1/org/nodes/{child.json()['id']}").status_code == 200
        assert c.delete(f"/api/v1/org/nodes/{r.json()['id']}").status_code == 200


def test_org_reads_public_writes_require_login(tmp_path):
    with TestClient(_app(tmp_path)) as c:
        assert c.get("/api/v1/org").status_code == 200
        assert c.post("/api/v1/org/nodes", json={"code": "OPS"}).status_code == 401
