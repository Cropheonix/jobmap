"""SearXNG 后端适配器（嵌入式 127.0.0.1:8889 或用户外部实例）。

限流治理（实测教训：单任务高频查询会触发上游引擎 Suspended）：
- 查询节流：连续查询最小间隔 QUERY_GAP 秒；
- 引擎冷却记账：unresponsive 中的 Suspended 引擎进入冷却期，冷却中的引擎从请求集合剔除；
- 限定引擎全部冷却 → 回退实例默认引擎池；仍无结果且存在 Suspended → 明确报错。
"""
from __future__ import annotations

import time
from urllib.parse import urlparse

import httpx

from backend.discovery.base import DiscoveryBackend, DiscoveryError, DiscoveryHit

QUERY_GAP = 2.5          # 连续查询最小间隔（秒）
ENGINE_COOLDOWN = 900.0  # 引擎冷却期（15 分钟，Suspended 恢复周期量级）


class SearxngBackend(DiscoveryBackend):
    name = "searxng"

    def __init__(self, base_url: str, timeout: float = 15.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        # 本机/自托管服务：不继承系统代理，避免 127.0.0.1 被代理劫持
        self._client = httpx.Client(timeout=timeout, trust_env=False,
                                    headers={"User-Agent": "JobMap/1.0 discovery"})
        self._last_query_ts = 0.0
        self._engine_cooldown: dict[str, float] = {}
        self.query_count = 0

    # ── 限流治理 ────────────────────────────────────────────
    def _throttle(self) -> None:
        wait = self._last_query_ts + QUERY_GAP - time.monotonic()
        if wait > 0:
            time.sleep(min(wait, 10.0))
        self._last_query_ts = time.monotonic()

    def _live_engines(self, engines: list[str] | None) -> list[str] | None:
        if not engines:
            return None
        now = time.monotonic()
        live = [e for e in engines if now >= self._engine_cooldown.get(e, 0)]
        return live or None  # 全部冷却 → None（回退默认池）

    def _record_cooldowns(self, unresponsive: list) -> None:
        now = time.monotonic()
        for item in unresponsive or []:
            name = item[0] if isinstance(item, (list, tuple)) and item else str(item)
            reason = str(item[1]).lower() if isinstance(item, (list, tuple)) and len(item) > 1 else ""
            if "suspend" in reason or "too many" in reason:
                self._engine_cooldown[name] = now + ENGINE_COOLDOWN

    def cooling_engines(self) -> list[str]:
        now = time.monotonic()
        return [e for e, until in self._engine_cooldown.items() if now < until]

    def search(self, query: str, *, engines: list[str] | None = None,
               pageno: int = 1, language: str = "zh-CN") -> list[DiscoveryHit]:
        engines = self._live_engines(engines)
        hits, unresponsive = self._search_once(query, engines=engines, pageno=pageno, language=language)
        self._record_cooldowns(unresponsive)
        if not hits and engines:
            # 引擎可用性因网络环境而异：限定引擎无结果时回退到实例默认引擎池
            hits, unresponsive = self._search_once(query, engines=None, pageno=pageno, language=language)
            self._record_cooldowns(unresponsive)
        if not hits:
            suspended = [u[0] for u in (unresponsive or [])
                         if len(u) > 1 and "suspend" in str(u[1]).lower()]
            if suspended:
                raise DiscoveryError(
                    f"上游引擎限流冷却中（{', '.join(suspended[:3])}），请稍后重试或减少查询频率")
        return hits

    def _search_once(self, query: str, *, engines: list[str] | None,
                     pageno: int, language: str) -> tuple[list[DiscoveryHit], list]:
        self._throttle()
        self.query_count += 1
        params: dict = {"q": query, "format": "json", "language": language, "pageno": pageno}
        if engines:
            params["engines"] = ",".join(engines)
        try:
            resp = self._client.get(f"{self.base_url}/search", params=params)
        except httpx.HTTPError as exc:
            raise DiscoveryError(f"SearXNG 不可达（{type(exc).__name__}）：{exc}") from exc
        if resp.status_code == 403:
            raise DiscoveryError("SearXNG 拒绝请求（403）：实例未开启 JSON 格式或已限流")
        if resp.status_code == 429:
            raise DiscoveryError("SearXNG 限流（429），稍后重试")
        if resp.status_code >= 400:
            raise DiscoveryError(f"SearXNG 返回 {resp.status_code}")
        try:
            data = resp.json()
        except ValueError as exc:
            raise DiscoveryError("SearXNG 返回非 JSON（实例未开启 json 格式？）") from exc

        hits: list[DiscoveryHit] = []
        for item in data.get("results", [])[:30]:
            url = item.get("url") or ""
            if not url:
                continue
            hits.append(DiscoveryHit(
                title=(item.get("title") or "").strip(),
                url=url,
                snippet=(item.get("content") or "").strip(),
                engine=item.get("engine") or "",
                site=urlparse(url).netloc.lower(),
                published_date=item.get("publishedDate"),
                score=item.get("score"),
            ))
        return hits, data.get("unresponsive_engines", []) or []

    def health(self) -> bool:
        try:
            resp = self._client.get(f"{self.base_url}/search",
                                    params={"q": "ping", "format": "json"}, timeout=8)
            return resp.status_code == 200
        except httpx.HTTPError:
            return False
