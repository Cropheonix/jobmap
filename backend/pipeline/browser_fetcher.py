"""隐身浏览器抓取通道（白名单站点：JS 渲染页与风控拦截页）。

引擎优先级：patchright（隐藏 CDP 自动化指纹，2026-11-05 实测通过 BOSS JS 挑战）
> 普通 Playwright（headless）> 不可用时降级 httpx/摘要。
设计边界（docs/discovery-fetch-design.md §5 / D7）：
- 仅白名单域名（zhipin.com / zhihu.com 等），低频（间隔 ≥2s）、并发 1；
- 访问 robots 允许的公开详情页；遇人工验证码仍然标记放弃，不做任何自动交互（不点验证码、不登录）。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from backend.pipeline.fetcher import FetchResult

_LOGIN_URL_MARKERS = ("login", "signin", "passport.", "account/verify")
_CAPTCHA_TEXT = ("验证码", "安全验证", "异常流量", "滑动验证", "请完成验证")


def _import_engine():
    """优先 patchright（隐身），fallback 普通 playwright。返回 (模块, 引擎名)。"""
    try:
        from patchright.sync_api import sync_playwright as sp
        return sp, "patchright"
    except ImportError:
        pass
    try:
        from playwright.sync_api import sync_playwright as sp
        return sp, "playwright"
    except ImportError:
        return None, None


def engine_name() -> str:
    _, name = _import_engine()
    return name or "unavailable"


def playwright_available() -> bool:
    return _import_engine()[0] is not None


@dataclass
class BrowserFetcher:
    min_interval: float = 2.0
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
        engine, self.engine = _import_engine()
        if engine is None:
            raise RuntimeError("浏览器引擎不可用（pip install patchright && python -m patchright install chromium）")
        # patchright 隐身模式需 headless=False（真实窗口，隐藏 CDP 指纹；普通 playwright 用 headless）
        headless = self.engine != "patchright"
        self._pw = engine().start()
        self._browser = self._pw.chromium.launch(headless=headless)
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
        # captcha 只在「小页面+挑战标记」时判（大页面正文含「验证码登录」导航链接是误报，2026-11-05 实测教训）
        if len(result.html) < 15000 and any(m in result.html for m in _CAPTCHA_TEXT):
            result.flags.append("captcha")
        hard = bool({"captcha", "login_wall"} & set(result.flags)) or result.status >= 400
        # 间歇性风控站（BOSS 等）：隐身浏览器有时过有时被踢到登录页——
        # 登录墙不算硬失败（下次任务可能就过了），只标记不 ok
        result.ok = not hard and bool(result.html) and len(result.html) > 20000  # <20KB 视为挑战壳页
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
