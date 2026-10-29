"""SearXNG 后端适配器（嵌入式 127.0.0.1:8889 或用户外部实例）。"""
from __future__ import annotations

from urllib.parse import urlparse

import httpx

from backend.discovery.base import DiscoveryBackend, DiscoveryError, DiscoveryHit


class SearxngBackend(DiscoveryBackend):
    name = "searxng"

    def __init__(self, base_url: str, timeout: float = 15.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        # 本机/自托管服务：不继承系统代理，避免 127.0.0.1 被代理劫持
        self._client = httpx.Client(timeout=timeout, trust_env=False,
                                    headers={"User-Agent": "JobMap/1.0 discovery"})

    def search(self, query: str, *, engines: list[str] | None = None,
               pageno: int = 1, language: str = "zh-CN") -> list[DiscoveryHit]:
        hits, unresponsive = self._search_once(query, engines=engines, pageno=pageno, language=language)
        if not hits and engines:
            # 引擎可用性因网络环境而异：限定引擎无结果时回退到实例默认引擎池
            hits, unresponsive = self._search_once(query, engines=None, pageno=pageno, language=language)
        if not hits:
            suspended = [u[0] for u in (unresponsive or [])
                         if len(u) > 1 and "suspend" in str(u[1]).lower()]
            if suspended:
                raise DiscoveryError(
                    f"上游引擎限流冷却中（{', '.join(suspended[:3])}），请稍后重试或减少查询频率")
        return hits

    def _search_once(self, query: str, *, engines: list[str] | None,
                     pageno: int, language: str) -> tuple[list[DiscoveryHit], list]:
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
