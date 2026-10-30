"""Playwright 真浏览器抓取通道（白名单站点：JS 渲染页与风控拦截页的合规回退）。

设计边界（docs/discovery-fetch-design.md §5 / D7）：
- 仅白名单域名（zhipin.com / zhihu.com 及政府 JS 页），低频（间隔 ≥3s）、并发 1；
- 真实 Chromium 渲染，等同人工浏览；遇到登录墙/验证码仍然标记并放弃，不做任何自动交互（不点验证码、不登录）；
- playwright 未安装或 chromium 未下载时自动降级为不可用（调用方回退 httpx/摘要路径）。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from backend.pipeline.fetcher import FetchResult

_LOGIN_URL_MARKERS = ("login", "signin", "passport.", "account/verify")
_CAPTCHA_TEXT = ("验证码", "安全验证", "异常流量", "滑动验证", "请完成验证")


def playwright_available() -> bool:
    try:
        from playwright.sync_api import sync_playwright  # noqa: F401
    except ImportError:
        return False
    try:
        from pathlib import Path
        from playwright._impl._driver import compute_driver_executable  # type: ignore
        return compute_driver_executable().exists()
    except Exception:
        return True


@dataclass
class BrowserFetcher:
    min_interval: float = 3.0
    page_timeout_ms: int = 20000
    settle_ms: int = 1500
    _pw = None
    _browser = None
    _page = None
    _next_allowed: dict = field(default_factory=dict)

    @property
    def available(self) -> bool:
        return playwright_available()

    def _ensure(self):
        if self._page is not None:
            return
        from playwright.sync_api import sync_playwright
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(headless=True)
        ctx = self._browser.new_context(
            locale="zh-CN",
            viewport={"width": 1366, "height": 900},
            user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"),
        )
        self._page = ctx.new_page()

    def fetch(self, url: str) -> FetchResult:
        import time as _time
        from urllib.parse import urlparse

        domain = urlparse(url).netloc
        wait = self._next_allowed.get(domain, 0) - _time.monotonic()
        if wait > 0:
            _time.sleep(min(wait, 30.0))
        self._next_allowed[domain] = _time.monotonic() + self.min_interval

        result = FetchResult(ok=False, url=url)
        try:
            self._ensure()
            resp = self._page.goto(url, wait_until="domcontentloaded",
                                   timeout=self.page_timeout_ms)
            self._page.wait_for_timeout(self.settle_ms)
            result.status = resp.status if resp else 0
            result.final_url = self._page.url
            result.html = self._page.content()[:524288]  # 单页上限 512KB
        except Exception as exc:  # 渲染失败/超时等，一律如实降级
            result.reason = f"browser: {type(exc).__name__}: {str(exc)[:120]}"
            return result

        final = (result.final_url or "").lower()
        if any(m in final for m in _LOGIN_URL_MARKERS):
            result.flags.append("login_wall")
        if any(m in result.html[:20000] for m in _CAPTCHA_TEXT):
            result.flags.append("captcha")
        hard = bool({"captcha", "login_wall"} & set(result.flags)) or result.status >= 400
        result.ok = not hard and bool(result.html)
        if not result.ok:
            result.reason = f"status={result.status} flags={','.join(result.flags) or '-'}"
        return result

    def close(self) -> None:
        for closer in (getattr(self, "_page", None) and self._page.close,
                       getattr(self, "_browser", None) and self._browser.close,
                       getattr(self, "_pw", None) and self._pw.stop):
            if closer:
                try:
                    closer()
                except Exception:
                    pass
        self._page = self._browser = self._pw = None
