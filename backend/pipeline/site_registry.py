"""站点注册表：域名 → 页面类型模式 → 解析器/抓取方式（URL 分诊器）。

robots_verdict 记录最近一次实测结论；运行时始终以实时 robots 解析为准（缓存 7 天）。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import yaml

REGISTRY_PATH = Path(__file__).parent.parent / "providers" / "site_registry.yaml"


@dataclass
class PageRule:
    pattern: re.Pattern
    page_type: str          # job_page / review_page / legal_page / gov_page
    parser: str             # jsonld / zhipin_job / zhaopin_job / gov_notice / tieba_thread / zhihu_question / generic
    fetcher: str            # httpx / playwright / none


@dataclass
class SiteEntry:
    domain: str
    rules: list[PageRule]
    min_interval: float
    robots_verdict: str = ""


class SiteRegistry:
    def __init__(self, data: dict | None = None):
        self._sites: dict[str, SiteEntry] = {}
        self._load(data or _load_yaml())

    def _load(self, data: dict) -> None:
        for domain, cfg in (data.get("domains") or {}).items():
            rules = []
            for page in cfg.get("pages", []):
                rules.append(PageRule(
                    pattern=re.compile(page["pattern"]),
                    page_type=page.get("type", "unknown"),
                    parser=page.get("parser", "generic"),
                    fetcher=page.get("fetcher", "httpx"),
                ))
            self._sites[domain.lower()] = SiteEntry(
                domain=domain.lower(),
                rules=rules,
                min_interval=float(cfg.get("min_interval", 3.0)),
                robots_verdict=cfg.get("robots_verdict", ""),
            )

    def triage(self, url: str) -> tuple[PageRule | None, SiteEntry | None]:
        """返回 (命中的页面规则, 站点条目)。未注册域名 → (None, None)。"""
        host = (urlparse(url).netloc or "").lower()
        host = host[4:] if host.startswith("www.") else host
        entry = self._sites.get(host)
        if not entry:
            # 移动端子域（m.zhipin.com）回落主域
            for domain, e in self._sites.items():
                if host.endswith("." + domain):
                    entry = e
                    break
        if not entry:
            return None, None
        path = urlparse(url).path or "/"
        for rule in entry.rules:
            if rule.pattern.search(path):
                return rule, entry
        return None, entry

    def all_job_domains(self) -> list[str]:
        out = []
        for entry in self._sites.values():
            if any(r.page_type == "job_page" for r in entry.rules):
                out.append(entry.domain)
        return out


def _load_yaml() -> dict:
    if not REGISTRY_PATH.exists():
        return {}
    with open(REGISTRY_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}
