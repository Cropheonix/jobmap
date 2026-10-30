"""产品化功能测试：OrgWatch 调度状态 + 隔离区恢复 + 视图参数。"""
from backend.core.scheduler import _load_state, _save_state, _watch_due
from backend.db.database import Base, engine
from backend.db.seed import ensure_seeded

Base.metadata.create_all(engine)
ensure_seeded()


def test_watch_state_roundtrip(tmp_path):
    from backend.core import scheduler
    orig = scheduler._STATE_PATH
    scheduler._STATE_PATH = tmp_path / "watch-state.json"
    try:
        assert _load_state()["last_run"] is None
        assert not _watch_due(7)  # 从未运行过不自动首跑
        _save_state({"last_run": "2026-10-01T00:00:00", "last_summary": None})
        state = _load_state()
        assert state["last_run"] == "2026-10-01T00:00:00"
    finally:
        scheduler._STATE_PATH = orig


def test_restore_endpoint():
    from fastapi.testclient import TestClient
    import backend.main as m

    with TestClient(m.app) as client:
        token = client.get("/api/session/config").json()["token"]
        h = {"X-JobMap-Token": token}
        # 造一条隔离记录
        import json as _json
        payload = {"jobs": [{
            "organization": {"name": "隔离恢复测试公司", "type": "company"},
            "title": "测试隔离岗位", "city": "成都",
            "sources": [{"provider": "测试", "url": "https://example.com/restore-1",
                         "source_type": "user_import"}],
            "status": "unverified"}]}
        r = client.post("/api/import", files={"file": ("t.json",
                     _json.dumps(payload).encode(), "application/json")}, headers=h)
        assert r.status_code == 200
        # 通过 DB 直接置为隔离区（模拟筛选拒绝）
        from backend.db.database import db_scope
        from backend.db.models import Job
        from sqlalchemy import select
        with db_scope() as db:
            job = db.execute(select(Job).where(Job.title == "测试隔离岗位")).scalars().first()
            jid = job.id
            job.filter_zone = "quarantined"
            job.rejection_reason = "low_relevance"
        # 恢复
        r2 = client.post(f"/api/jobs/{jid}/restore", headers=h)
        assert r2.status_code == 200
        assert r2.json()["filter_zone"] == "accepted_suspect"
        assert r2.json()["rejection_reason"] == ""
        # 重复恢复 → 422
        r3 = client.post(f"/api/jobs/{jid}/restore", headers=h)
        assert r3.status_code == 422
        # 无令牌 → 403
        r4 = client.post(f"/api/jobs/{jid}/restore")
        assert r4.status_code == 403


def test_quarantine_zone_filter():
    from fastapi.testclient import TestClient
    import backend.main as m

    with TestClient(m.app) as client:
        board = client.get("/api/jobs?zone=active&limit=2000").json()
        quarantine = client.get("/api/jobs?zone=quarantine&limit=2000").json()
        assert all(i["filter_zone"] != "quarantined" for i in board["items"])
        assert all(i["filter_zone"] == "quarantined" for i in quarantine["items"])
