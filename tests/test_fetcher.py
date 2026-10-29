"""抓取层测试：robots 感知、限速、登录墙/验证码检测。"""
import httpx

from backend.pipeline.fetcher import Fetcher
from backend.pipeline.site_registry import SiteRegistry

ROBOTS = "User-agent: *\nDisallow: /private/\nAllow: /\n"


def _client_with(routes: dict[str, httpx.Response]):
    def handler(request: httpx.Request) -> httpx.Response:
        for key, resp in routes.items():
            if key in str(request.url):
                return resp
        return httpx.Response(404)
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_robots_disallow_blocks_fetch():
    fetcher = Fetcher(client=_client_with({"robots.txt": httpx.Response(200, text=ROBOTS)}))
    ok, why = fetcher.can_fetch("https://example.com/private/page")
    assert not ok and "robots" in why
    ok, _ = fetcher.can_fetch("https://example.com/public/page")
    assert ok


def test_login_wall_detected():
    fetcher = Fetcher(min_interval=0, client=_client_with({
        "job/1": httpx.Response(200, text="<html><body>请登录后查看职位详情</body></html>"),
    }))
    result = fetcher.fetch("https://example.com/job/1")
    assert not result.ok
    assert "login_wall" in result.flags


def test_captcha_detected():
    fetcher = Fetcher(min_interval=0, client=_client_with({
        "job/2": httpx.Response(403, text="请完成安全验证"),
    }))
    result = fetcher.fetch("https://example.com/job/2")
    assert not result.ok and "captcha" in result.flags


def test_normal_page_ok():
    html = "<html><body>" + "职位详情内容。" * 5000 + "</body></html>"
    fetcher = Fetcher(min_interval=0, client=_client_with({
        "job/3": httpx.Response(200, text=html),
    }))
    result = fetcher.fetch("https://example.com/job/3")
    assert result.ok and result.html


def test_site_registry_triage():
    registry = SiteRegistry()
    rule, entry = registry.triage("https://www.zhipin.com/job_detail/abc123.html")
    assert rule is not None and rule.page_type == "job_page"
    rule2, _ = registry.triage("https://www.zhipin.com/job_detail/abc.html?query=1")
    assert rule2 is not None  # 路径部分仍匹配；robots 由 Fetcher 实时判定 query 规则
    rule3, _ = registry.triage("https://unknown-site.example.com/x")
    assert rule3 is None
    # 移动端子域回落
    rule4, _ = registry.triage("https://m.zhipin.com/job_detail/xyz.html")
    assert rule4 is not None
