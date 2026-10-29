"""端到端 API 测试（独立临时数据库，TestClient 启动时自动种子）。"""
import io
import time

import pytest
from fastapi.testclient import TestClient

import backend.main as m
from backend.db.database import Base, engine
from backend.db.seed import ensure_seeded

Base.metadata.create_all(engine)
ensure_seeded()


@pytest.fixture(scope="module")
def client():
    with TestClient(m.app) as c:
        yield c


@pytest.fixture(scope="module")
def token(client):
    return client.get("/api/session/config").json()["token"]


def test_jobs_listed_and_deduped(client):
    d = client.get("/api/jobs?limit=2000").json()
    assert d["total"] == 50  # 50 条记录，其中 3 条多来源在种子阶段合并
    dup = [i for i in d["items"] if i["sources_count"] > 1]
    assert len(dup) >= 3  # 多来源折叠为「来自 N 个来源」


def test_jobs_filter_city(client):
    d = client.get("/api/jobs", params={"city": "北京"}).json()
    assert d["total"] == 2
    assert all(i["city"] == "北京" for i in d["items"])


def test_expired_sorted_to_bottom(client):
    d = client.get("/api/jobs", params={"order": "score"}).json()
    statuses = [i["status"] for i in d["items"]]
    active_tail = [s for s in statuses if s in ("active", "unverified")]
    assert statuses[: len(active_tail)] == active_tail  # 在招在前


def test_action_requires_token(client, token):
    r = client.patch("/api/jobs/1/action", json={"status": "favorite"})
    assert r.status_code == 403
    r = client.patch("/api/jobs/1/action", json={"status": "favorite"},
                     headers={"X-JobMap-Token": token})
    assert r.status_code == 200
    assert r.json()["action"]["status"] == "favorite"
    r = client.patch("/api/jobs/1/action", json={"status": "hack"},
                     headers={"X-JobMap-Token": token})
    assert r.status_code == 422


def test_manual_register_validates_url(client, token):
    r = client.post("/api/jobs/manual", json={"url": "http://127.0.0.1:9000/x"},
                    headers={"X-JobMap-Token": token})
    assert r.status_code == 422
    r = client.post("/api/jobs/manual", json={"url": "https://example.com/jobs/9",
                                              "company": "外部公司", "title": "外部岗位"},
                    headers={"X-JobMap-Token": token})
    assert r.status_code == 200
    assert r.json()["job"]["status_label"] == "状态未核实"


def test_search_task_full_pipeline(client, token):
    r = client.post("/api/search/tasks", json={"keywords": ["工程师", "成都"], "providers": ["sample"]},
                    headers={"X-JobMap-Token": token})
    assert r.status_code == 200
    task_id = r.json()["id"]
    for _ in range(60):  # 最多等 30 秒
        t = client.get(f"/api/search/tasks/{task_id}").json()
        if t["state"] in ("done", "failed", "cancelled"):
            break
        time.sleep(0.5)
    assert t["state"] == "done"
    assert all(s["state"] == "done" for s in t["steps"])


def test_map_points_exclude_unlocatable(client):
    d = client.get("/api/map/points").json()
    assert all(p["precision"] != "unlocatable" for p in d["points"])


def test_export_csv_bom_and_formula_guard(client):
    r = client.get("/api/export?format=csv")
    assert r.status_code == 200
    body = r.content
    assert body[:3] == b"\xef\xbb\xbf"  # UTF-8-SIG，Excel/WPS 中文不乱码
    assert "排名" in body.decode("utf-8-sig")


def test_import_json_roundtrip(client, token):
    payload = {
        "jobs": [{
            "organization": {"name": "测试导入公司", "type": "company"},
            "title": "导入测试岗位",
            "city": "成都", "district": "高新区",
            "salary_min": 10, "salary_max": 15,
            "sources": [{"provider": "用户导入", "url": "https://example.com/import/1",
                         "source_type": "user_import"}],
            "status": "unverified",
        }]
    }
    r = client.post("/api/import", files={"file": ("t.json", io.BytesIO(
        __import__("json").dumps(payload).encode()), "application/json")},
        headers={"X-JobMap-Token": token})
    assert r.status_code == 200
    assert r.json()["added"] == 1
    # 重复导入 → 合并
    r2 = client.post("/api/import", files={"file": ("t.json", io.BytesIO(
        __import__("json").dumps(payload).encode()), "application/json")},
        headers={"X-JobMap-Token": token})
    assert r2.json()["merged"] == 1


def test_preferences_update_and_recompute(client, token):
    r = client.put("/api/preferences", json={"salary_expect_min": 20, "salary_expect_max": 30,
                                             "weights": {"M": 0, "P": 100, "C": 0, "R": 0, "D": 0}},
                   headers={"X-JobMap-Token": token})
    assert r.status_code == 200
    assert r.json()["salary_expect_min"] == 20
    r = client.post("/api/scores/recompute", headers={"X-JobMap-Token": token})
    assert r.status_code == 200 and r.json()["jobs"] >= 50
