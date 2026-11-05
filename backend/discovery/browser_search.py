"""浏览器直接搜索引擎（T2.5 层）：用 patchright 隐身浏览器像真人一样搜索 Bing/Google。

优势（2026-11-05 实测）：
- 不需要 SearXNG 中转、不需要 API Key、不受引擎 API 限流
- Bing 实测：搜索框输入 + 回车 → 86,700 条结果，含 BOSS 结构化链接
- Google 实测：先访问首页热身获取 cookie → 搜索 → 通过（无验证码）
- 引擎看到的是一个真实 Chromium 浏览器的正常搜索行为
"""
from __future__ import annotations

import re
import time
from urllib.parse import urlparse

from backend.discovery.base import DiscoveryBackend, DiscoveryError, DiscoveryHit


class BrowserSearchBackend(DiscoveryBackend):
    """用 patchright 隐身浏览器直接搜索（Bing 优先，Google 热身后备用）。"""

    name = "browser_search"

    def __init__(self, timeout: float = 25.0):
        self.timeout = timeout
        self._pw = None
        self._browser = None
        self._page = None
        self._warmed = {"bing": False, "google": False}
        self.query_count = 0

    def _ensure(self):
        if self._page is not None:
            return
        try:
            from patchright.sync_api import sync_playwright
        except ImportError:
            try:
                from playwright.sync_api import sync_playwright
            except ImportError:
                raise DiscoveryError("patchright/playwright 未安装（pip install patchright）")
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(headless=False)
        ctx = self._browser.new_context(locale="zh-CN", viewport={"width": 1366, "height": 900})
        self._page = ctx.new_page()

    def _warm(self, engine: str):
        """访问搜索引擎首页获取 cookie（模拟真人打开浏览器）。"""
        if self._warmed.get(engine):
            return
        urls = {"bing": "https://www.bing.com", "google": "https://www.google.com"}
        try:
            self._page.goto(urls[engine], wait_until="domcontentloaded", timeout=self.timeout * 1000)
            self._page.wait_for_timeout(1500)
            self._warmed[engine] = True
        except Exception:
            pass

    def search(self, query: str, *, engines: list[str] | None = None,
               pageno: int = 1, language: str = "zh-CN") -> list[DiscoveryHit]:
        self._ensure()
        self.query_count += 1
        time.sleep(2.0)  # 搜索间隔（礼貌限速）

        # 引擎优先级：bing（宽容）> google（需热身）
        engine = "bing"
        if engines and "google" in engines and "bing" not in engines:
            engine = "google"

        try:
            return self._search_bing(query, pageno) if engine == "bing" else self._search_google(query, pageno)
        except DiscoveryError:
            raise
        except Exception as exc:
            raise DiscoveryError(f"浏览器搜索失败（{engine}）: {type(exc).__name__}: {str(exc)[:100]}")

    def _search_bing(self, query: str, pageno: int) -> list[DiscoveryHit]:
        self._warm("bing")
        if pageno == 1:
            # 用搜索框输入（模拟真人）
            self._page.goto("https://www.bing.com", wait_until="domcontentloaded",
                           timeout=self.timeout * 1000)
            self._page.wait_for_timeout(500)
            box = self._page.locator("#sb_form_q, input[name='q']").first
            if box.count() > 0:
                box.fill(query)
                box.press("Enter")
                self._page.wait_for_load_state("domcontentloaded")
            else:
                self._page.goto(f"https://www.bing.com/search?q={query}",
                               wait_until="domcontentloaded", timeout=self.timeout * 1000)
        else:
            self._page.goto(f"https://www.bing.com/search?q={query}&first={(pageno-1)*10+1}",
                           wait_until="domcontentloaded", timeout=self.timeout * 1000)
        self._page.wait_for_timeout(2500)
        return self._parse_bing_serp(self._page.content())

    def _search_google(self, query: str, pageno: int) -> list[DiscoveryHit]:
        self._warm("google")
        start = (pageno - 1) * 10
        self._page.goto(f"https://www.google.com/search?q={query}&start={start}&hl=zh-CN",
                       wait_until="domcontentloaded", timeout=self.timeout * 1000)
        self._page.wait_for_timeout(2500)
        if "sorry" in self._page.url or "recaptcha" in self._page.content()[:5000].lower():
            raise DiscoveryError("Google 出现验证码（浏览器 session 已被标记，建议用 Bing）")
        return self._parse_google_serp(self._page.content())

    def _parse_bing_serp(self, html: str) -> list[DiscoveryHit]:
        """解析 Bing SERP 页面 → DiscoveryHit 列表。"""
        hits: list[DiscoveryHit] = []
        seen: set[str] = set()
        # Bing 结果结构：<h2><a href="url">title</a></h2>
        for m in re.finditer(r'<h2[^>]*><a[^>]+href="([^"]+)"[^>]*>(.*?)</a></h2>', html, re.DOTALL):
            url = m.group(1).split("?")[0].split("#")[0]
            title = re.sub(r"<[^>]+>", "", m.group(2)).strip()
            if not url.startswith("http") or url in seen or not title:
                continue
            seen.add(url)
            hits.append(DiscoveryHit(
                title=title, url=url, snippet="",
                engine="bing",
                site=urlparse(url).netloc.lower(),
            ))
        return hits[:30]

    def _parse_google_serp(self, html: str) -> list[DiscoveryHit]:
        """解析 Google SERP 页面。"""
        hits: list[DiscoveryHit] = []
        seen: set[str] = set()
        # Google 结果链接在 <a href="/url?q=actual_url"> 或直接 href
        for m in re.finditer(r'<a[^>]+href="(?:/url\?q=|)(https?://[^"&]+)[^"]*"[^>]*>.*?<h3[^>]*>(.*?)</h3>', html, re.DOTALL):
            url = m.group(1).split("?")[0].split("#")[0]
            title = re.sub(r"<[^>]+>", "", m.group(2)).strip()
            if "google.com" in url or url in seen or not title:
                continue
            seen.add(url)
            hits.append(DiscoveryHit(
                title=title, url=url, snippet="",
                engine="google",
                site=urlparse(url).netloc.lower(),
            ))
        return hits[:30]

    def health(self) -> bool:
        try:
            self._ensure()
            return True
        except Exception:
            return False

    def close(self):
        for closer in (getattr(self, "_page", None) and self._page.close,
                       getattr(self, "_browser", None) and self._browser.close,
                       getattr(self, "_pw", None) and self._pw.stop):
            if closer:
                try:
                    closer()
                except Exception:
                    pass
        self._page = self._browser = self._pw = None
