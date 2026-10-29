"""发现层测试：SearXNG 后端解析与查询规划器。"""
import httpx
import pytest

from backend.discovery.base import DiscoveryError
from backend.discovery.planner import job_queries, review_queries
from backend.discovery.searxng import SearxngBackend
from backend.filters.intent import parse_intent

_FIXTURE = {
    "results": [
        {"title": "成都后端工程师-星澜科技", "url": "https://www.zhipin.com/job_detail/abc123.html",
         "content": "15-25K·14薪", "engine": "bing", "score": 1.0},
        {"title": "后端开发", "url": "https://www.zhaopin.com/jobs/12345/", "content": "",
         "engine": "baidu"},
    ],
    "unresponsive_engines": [["brave", "Suspended"]],
}


def _mock_client(fixture=None, status=200):
    def handler(request):
        return httpx.Response(status, json=fixture if fixture is not None else {})
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_searxng_search_parses_hits():
    backend = SearxngBackend("http://127.0.0.1:18888")
    backend._client = _mock_client(_FIXTURE)
    hits = backend.search("成都 后端", engines=["bing", "baidu"])
    assert len(hits) == 2
    assert hits[0].site == "www.zhipin.com"
    assert hits[0].engine == "bing"
    assert hits[1].url.endswith("/jobs/12345/")


def test_searxng_empty_restricted_engines_falls_back():
    """限定引擎无结果 → 自动回退默认引擎池（引擎可用性因网络而异）。"""
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if "engines=" in str(request.url):
            return httpx.Response(200, json={"results": []})  # 限定引擎全部无结果
        return httpx.Response(200, json=_FIXTURE)              # 默认池有结果

    backend = SearxngBackend("http://127.0.0.1:18888")
    backend._client = httpx.Client(transport=httpx.MockTransport(handler))
    hits = backend.search("成都 后端", engines=["bing"])
    assert len(hits) == 2
    assert len(calls) == 2 and "engines=" not in calls[1]


def test_searxng_unavailable_raises():
    backend = SearxngBackend("http://127.0.0.1:1")  # 无监听端口
    with pytest.raises(DiscoveryError):
        backend.search("test")


def test_searxng_403_means_json_disabled():
    backend = SearxngBackend("http://127.0.0.1:18888")
    backend._client = _mock_client(status=403)
    with pytest.raises(DiscoveryError, match="JSON"):
        backend.search("test")


def test_planner_job_queries():
    it = parse_intent("成都 后端")
    plans = job_queries(it, site_domains=["zhipin.com", "zhaopin.com"])
    texts = [p.query for p in plans]
    assert any("site:zhipin.com" in t for t in texts)
    assert any("site:zhaopin.com" in t for t in texts)
    # 同词去重
    assert len(texts) == len(set(texts))
    # 优先级排序：基础词在前
    assert plans[0].priority == 0


def test_planner_review_queries():
    plans = review_queries("星澜科技")
    assert len(plans) == 6  # 3 站点 × 2 词
    assert all("星澜科技" in p.query for p in plans)
