"""抓取执行器：robots 感知 + 域名限速 + 登录墙/验证码检测 + 指数退避。

礼貌策略（对应设计 §5）：单域名并发 1、间隔 3s（gov 5s）、429/403 指数退避、
登录墙/验证码标记后停源，绝不规避。
"""
from __future__ import annotations

import time
import urllib.robotparser
from dataclasses import dataclass, field
from urllib.parse import urlparse

import httpx

USER_AGENT = "JobMap/1.0 (+local personal research; 127.0.0.1)"

_LOGIN_URL_MARKERS = ("login", "signin", "passport.", "account/verify")
_LOGIN_TEXT_MARKERS = ("登录后查看", "请登录", "扫码登录", "登录查看更多", "立即登录")
_CAPTCHA_TEXT_MARKERS = ("验证码", "安全验证", "异常流量", "滑动验证", "请完成验证")


@dataclass
class FetchResult:
    ok: bool
    url: str
    final_url: str = ""
    status: int = 0
    html: str = ""
    flags: list[str] = field(default_factory=list)
    reason: str = ""


class DomainBackoff:
    """域名级退避：连续失败进入冷却。"""

    def __init__(self):
        self._fails: dict[str, int] = {}
        self._cooldown_until: dict[str, float] = {}

    def record(self, domain: str, ok: bool) -> None:
        if ok:
            self._fails[domain] = 0
            return
        n = self._fails.get(domain, 0) + 1
        self._fails[domain] = n
        if n >= 3:
            self._cooldown_until[domain] = time.monotonic() + 10 * 60  # 10 分钟

    def cooling(self, domain: str) -> bool:
        return time.monotonic() < self._cooldown_until.get(domain, 0)


class Fetcher:
    def __init__(self, min_interval: float = 3.0, gov_interval: float = 5.0,
                 robots_ttl: float = 7 * 86400, client: httpx.Client | None = None):
        self.min_interval = min_interval
        self.gov_interval = gov_interval
        self.robots_ttl = robots_ttl
        self._client = client or httpx.Client(
            timeout=httpx.Timeout(10.0, connect=3.0),
            headers={"User-Agent": USER_AGENT}, follow_redirects=True)
        self._robots: dict[str, tuple[urllib.robotparser.RobotFileParser, float]] = {}
        self._next_allowed: dict[str, float] = {}
        self.backoff = DomainBackoff()

    # ── robots ────────────────────────────────────────────────
    def _robots_for(self, domain: str) -> urllib.robotparser.RobotFileParser | None:
        """robots 缓存 7 天；取不到（404/无文件）按允许处理但记录探测。"""
        now = time.monotonic()
        cached = self._robots.get(domain)
        if cached and now - cached[1] < self.robots_ttl:
            return cached[0]
        rp = urllib.robotparser.RobotFileParser()
        try:
            resp = self._client.get(f"https://{domain}/robots.txt")
            if resp.status_code == 200 and resp.text.strip():
                rp.parse(resp.text.splitlines())
            else:
                rp.parse([])  # 无 robots → 全允许
        except httpx.HTTPError:
            rp.parse([])
        self._robots[domain] = (rp, now)
        return rp

    def can_fetch(self, url: str) -> tuple[bool, str]:
        domain = urlparse(url).netloc
        if not domain:
            return False, "URL 无域名"
        if self.backoff.cooling(domain):
            return False, "域名冷却退避中"
        rp = self._robots_for(domain)
        if not rp.can_fetch(USER_AGENT, url) or not rp.can_fetch("*", url):
            return False, "robots 不允许"
        return True, "ok"

    # ── 抓取 ──────────────────────────────────────────────────
    def fetch(self, url: str) -> FetchResult:
        domain = urlparse(url).netloc
        interval = self.gov_interval if self._is_gov(domain) else self.min_interval
        wait = self._next_allowed.get(domain, 0) - time.monotonic()
        if wait > 0:
            time.sleep(min(wait, 30.0))
        self._next_allowed[domain] = time.monotonic() + interval

        result = FetchResult(ok=False, url=url)
        try:
            resp = self._client.get(url)
            result.status = resp.status_code
            result.final_url = str(resp.url)
            result.html = resp.text[:262144]  # 单页上限 256KB
        except httpx.HTTPError as exc:
            result.reason = f"{type(exc).__name__}: {exc}"
            self.backoff.record(domain, False)
            return result

        flags = self._detect_flags(result)
        result.flags = flags
        hard_block = bool({"captcha", "login_wall"} & set(flags)) or result.status in (403, 412, 429)
        result.ok = result.status == 200 and not hard_block
        self.backoff.record(domain, result.ok)
        if not result.ok:
            result.reason = f"status={result.status} flags={','.join(flags) or '-'}"
        return result

    def _detect_flags(self, result: FetchResult) -> list[str]:
        flags: list[str] = []
        final = (result.final_url or "").lower()
        if any(m in final for m in _LOGIN_URL_MARKERS):
            flags.append("login_wall")
        text = result.html or ""
        if len(text) < 20480 and any(m in text for m in _LOGIN_TEXT_MARKERS):
            flags.append("login_wall")
        if result.status in (403, 412, 429) or any(m in text for m in _CAPTCHA_TEXT_MARKERS):
            flags.append("captcha")
        return flags

    @staticmethod
    def _is_gov(domain: str) -> bool:
        return ".gov.cn" in domain or ".gov" in domain
