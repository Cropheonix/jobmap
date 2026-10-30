"""分层信源架构测试：T3 WAF 降级、T1 官网监测（快照对比/链接提取/Greenhouse）、T4 粘贴导入。"""
import httpx
import pytest
from fastapi.testclient import TestClient

import backend.main as m
from backend.db.database import Base, engine
from backend.db.seed import ensure_seeded
from backend.pipeline.fetcher import Fetcher, detect_waf_shell

Base.metadata.create_all(engine)
ensure_seeded()


@pytest.fixture(scope="module")
def client():
    with TestClient(m.app) as c:
        yield c


@pytest.fixture(scope="module")
def token(client):
    return client.get("/api/session/config").json()["token"]


# ── T3：WAF 壳页识别与永久降级 ─────────────────────────────
def test_waf_shell_by_marker():
    fr = Fetcher.__new__(Fetcher)
    from backend.pipeline.fetcher import FetchResult
    r = FetchResult(ok=True, url="https://x.com/a", html="<html><body>请稍候，正在验证…</body></html>")
    assert detect_waf_shell(r) is True


def test_waf_shell_by_empty_body():
    from backend.pipeline.fetcher import FetchResult
    r = FetchResult(ok=True, url="https://x.com/a",
                    html="<html><script>var x=1;</script><body></body></html>")
    assert detect_waf_shell(r) is True


def test_normal_page_not_waf():
    from backend.pipeline.fetcher import FetchResult
    html = "<html><body>" + "这是职位详情页内容。" * 200 + "</body></html>"
    r = FetchResult(ok=True, url="https://x.com/a", html=html)
    assert detect_waf_shell(r) is False


def test_domain_downgrade_persistent(client, token):
    from backend.providers.watch import domain_downgraded, mark_domain_downgraded
    mark_domain_downgraded("waf-test.example.com", "waf_shell;status=200")
    assert domain_downgraded("waf-test.example.com") is True
    assert domain_downgraded("other.example.com") is False


# ── T1：官网监测 ──────────────────────────────────────────
def test_extract_watch_links():
    from backend.providers.watch import _extract_watch_links
    html = """
    <a href="/jobs/123">设计师</a>
    <a href="https://corp.example.com/career/456">工程师</a>
    <a href="/about">关于</a>
    <a href="https://other.com/job/1">外站</a>
    <a href="/joinus?t=1#x">加入</a>
    """
    links = _extract_watch_links(html, "https://corp.example.com/careers")
    assert "https://corp.example.com/jobs/123" in links
    assert "https://corp.example.com/career/456" in links
    assert "https://corp.example.com/joinus?t=1" in links
    assert all(l.startswith("https://corp.example.com") for l in links)  # 外站/非招聘链接排除


def test_greenhouse_parser():
    from backend.providers.watch import _fetch_ats_board
    RealClient = httpx.Client  # 先绑定真类（watch_mod.httpx 与本模块是同一 httpx 对象）

    def handler(request: httpx.Request) -> httpx.Response:
        assert "boards-api.greenhouse.io" in str(request.url)
        return httpx.Response(200, json={"jobs": [
            {"title": "设计师", "absolute_url": "https://example.com/jobs/1"},
            {"title": "工程师", "absolute_url": "https://example.com/jobs/2"},
        ]})

    def fake_client(**kw):
        kw.pop("transport", None)
        return RealClient(transport=httpx.MockTransport(handler), **kw)

    import backend.providers.watch as watch_mod
    orig = watch_mod.httpx.Client
    watch_mod.httpx.Client = fake_client
    try:
        links, titles = _fetch_ats_board("greenhouse:testco")
        assert len(links) == 2 and titles[links[0]] == "设计师"
    finally:
        watch_mod.httpx.Client = orig


def test_watch_snapshot_diff(client, token):
    """官网监测：两次检查之间链接集合变化 → added/removed。"""
    import backend.providers.watch as watch_mod
    from backend.db.database import db_scope
    from backend.db.models import Organization

    pages = {1: '<a href="/job/a">A</a>', 2: '<a href="/job/a">A</a><a href="/job/b">B</a>'}
    state = {"n": 0}
    RealClient = httpx.Client

    def handler(request: httpx.Request) -> httpx.Response:
        state["n"] += 1
        return httpx.Response(200, text=pages[min(state["n"], 2)])

    def fake_client(**kw):
        kw.pop("transport", None)
        return RealClient(transport=httpx.MockTransport(handler), **kw)

    orig_client = watch_mod.httpx.Client
    watch_mod.httpx.Client = fake_client
    orig_ingest = watch_mod._ingest_watch_links
    watch_mod._ingest_watch_links = lambda *a, **k: None  # 单测不写 jobs
    try:
        with db_scope() as db:
            org = Organization(canonical_name="__watch_test__", recruiting_page="https://wt.example.com/careers")
            db.add(org)
            db.commit()
            oid = org.id
        r1 = watch_mod.check_organization(oid)
        assert r1["added"] == ["https://wt.example.com/job/a"]
        r2 = watch_mod.check_organization(oid)
        assert r2["added"] == ["https://wt.example.com/job/b"]      # 新增
        assert r2["removed"] == []                                  # a 仍在
    finally:
        watch_mod.httpx.Client = orig_client
        watch_mod._ingest_watch_links = orig_ingest


# ── T4：粘贴导入 ──────────────────────────────────────────
def test_import_text_paste(client, token):
    text = "商业空间设计师 10-18K·13薪\n方物空间设计（成都）有限公司 · 武侯区 · 3-5年 · 本科\n负责零售空间设计。"
    r = client.post("/api/import/text", json={"text": text, "city": "成都"},
                    headers={"X-JobMap-Token": token})
    assert r.status_code == 200
    body = r.json()
    assert body["result"] in ("created", "merged")
    jid = body["job_id"]
    d = client.get(f"/api/jobs/{jid}").json()
    assert d["salary_display"] == "10-18K·13薪"
    assert "方物空间设计" in d["organization"]["name"]
    assert d["discovered_via"] == "user_paste"


def test_import_text_url_validated(client, token):
    r = client.post("/api/import/text", json={"text": "http://127.0.0.1:9/x"},
                    headers={"X-JobMap-Token": token})
    assert r.status_code == 422  # 内网地址被拦


def test_import_text_requires_token(client):
    r = client.post("/api/import/text", json={"text": "随便"})
    assert r.status_code == 403
