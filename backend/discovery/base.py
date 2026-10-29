"""发现层抽象：可插拔搜索后端。"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class DiscoveryHit:
    title: str
    url: str
    snippet: str = ""
    engine: str = ""
    site: str = ""
    published_date: str | None = None
    score: float | None = None
    extra: dict = field(default_factory=dict)


class DiscoveryError(Exception):
    """发现层不可用（未启用/离线/被限流）。"""


class DiscoveryBackend:
    name = "abstract"

    def search(self, query: str, *, engines: list[str] | None = None,
               pageno: int = 1, language: str = "zh-CN") -> list[DiscoveryHit]:
        raise NotImplementedError

    def health(self) -> bool:
        return False
