"""远程.work 数据源：T1 直取层（远程/综合岗位，全行业）。

通道（2026-10-30 POC）：首页即列表（约 21 个职位直链 /数字.html），详情页公开、
无风控，标题格式「{公司}招聘全职/兼职远程工作{岗位}-远程.work」，页面含薪资区间。
本源不依赖搜索引擎配额。
"""
from __future__ import annotations

import re
from datetime import date
from urllib.parse import urljoin

import httpx

from backend.db.database import db_scope
from backend.filters import intent as intent_mod
from backend.filters.pipeline import run_filters
from backend.pipeline.extractor import extract_by_regex
from backend.pipeline.loader import upsert_job_record
from backend.providers.base import ProviderCapability, SearchProvider

_BASE = "https://yuancheng.work/"
_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/126.0.0.0"}
_DETAIL_RE = re.compile(r'href="(https?://yuancheng\.work/\d{4,6}\.html)"[^>]*>([^<]{4,60})</a>')
_TITLE_RE = re.compile(r"(?P<org>.+?)招聘(?:全职/?兼职|全职|兼职)?(?:远程工作)?(?P<role>.+?)(?:-远程\.work)?$")


def parse_detail_title(page_title: str) -> tuple[str, str]:
    t = (page_title or "").strip()
    m = _TITLE_RE.search(t)
    if m:
        return m.group("org").strip("0123456789招聘 "), m.group("role").strip()
    return "", t


def fetch_listing(client: httpx.Client) -> list[tuple[str, str]]:
    r = client.get(_BASE)
    r.raise_for_status()
    seen: dict[str, str] = {}
    for url, text in _DETAIL_RE.findall(r.text):
        url = url.split("?")[0]
        if url not in seen and text.strip():
            seen[url] = text.strip()
    return list(seen.items())[:24]


class RemoteWorkProvider(SearchProvider):
    capability = ProviderCapability(
        name="remote_work", display_name="远程.work（垂直站直取）", source_type="vertical_site",
        status="available", rate_limit="首页 1 次 + 详情 ≤24 页/任务，间隔 2s",
        license_notes="远程.work 公开职位页（T1 直取层），保留原文链接与归属。",
    )

    def check_policy(self, url: str) -> bool:
        return "yuancheng.work" in url

    def health_check(self) -> dict:
        try:
            with httpx.Client(timeout=12, headers=_HEADERS, follow_redirects=True) as c:
                jobs = fetch_listing(c)
            return {"name": self.capability.display_name, "status": "available",
                    "ok": bool(jobs), "records": len(jobs)}
        except Exception as exc:
            return {"name": self.capability.display_name, "status": "offline", "ok": False,
                    "error": str(exc)[:200]}

    def discover(self, query: dict, on_step=None, cancelled=None) -> dict:
        import time as _time

        counts = {"discovered": 0, "fetched": 0, "accepted": 0, "suspect": 0,
                  "quarantined": 0, "created": 0, "merged": 0, "errors": 0}

        def report(step="dedup", state="running"):
            if on_step:
                on_step({"step": step, "state": state, "counts": dict(counts)})

        report("discover")
        keywords = " ".join(query.get("keywords") or [])
        intent = intent_mod.parse_intent(f"{keywords} {query.get('city') or ''}".strip(),
                                         intent_mod.load_synonyms())
        today = date.today()
        with httpx.Client(timeout=15, headers=_HEADERS, follow_redirects=True) as c:
            try:
                listing = fetch_listing(c)
            except Exception as exc:
                counts["errors"] = 1
                counts["error_details"] = [f"远程.work 列表获取失败: {exc}"]
                return counts
            counts["discovered"] = len(listing)
            report("discover", "done")
            report("extract")

            for url, list_title in listing:
                if cancelled and cancelled():
                    break
                _time.sleep(2.0)  # 域名限速
                try:
                    resp = c.get(url)
                    if resp.status_code != 200:
                        continue
                    counts["fetched"] += 1
                    html = resp.text
                except httpx.HTTPError:
                    continue
                m = re.search(r"<title>([^<]{4,120})</title>", html)
                org, role = parse_detail_title(m.group(1) if m else list_title)
                blob = re.sub(r"<[^>]+>", " ", html)
                fields = extract_by_regex(blob)
                record = {
                    "organization": {"name": org or "远程.work 招聘方", "type": "company",
                                     "industry": "远程办公"},
                    "title": (role or list_title)[:80],
                    "description_summary": ("远程工作。" + (role or list_title))[:600],
                    "city": query.get("city") or "",
                    "salary_min": fields.get("salary_min"),
                    "salary_max": fields.get("salary_max"),
                    "salary_months": fields.get("salary_months"),
                    "job_category": "其他行业",
                    "industry": "远程办公",
                    "published_at": fields.get("published_at"),
                    "last_checked": today.isoformat(),
                    "keywords": [k for k in re.split(r"[·|｜,，/、\s]+", role or list_title)
                                 if 2 <= len(k) <= 12][:6],
                    "extraction_tier": "dom",
                    "sources": [{
                        "provider": "远程.work",
                        "url": url,
                        "source_type": "vertical_site",
                        "access_status": "accessible",
                        "observed_at": today.isoformat(),
                        "evidence_excerpt": (list_title or role)[:200],
                        "license_note": "远程.work 公开职位页，保留原文链接",
                    }],
                    "is_demo": False,
                    "discovered_via": "remote_work",
                }
                result = run_filters(record, intent, today=today)
                if result.zone == "quarantined" and result.rejection_reason == "missing_required":
                    counts["quarantined"] += 1
                    continue
                try:
                    with db_scope() as db:
                        outcome, job_row, _why = upsert_job_record(db, record)
                        if job_row is not None:
                            job_row.filter_zone = result.zone
                            job_row.confidence = result.confidence
                            job_row.rejection_reason = result.rejection_reason or ""
                            job_row.filter_trace = result.to_trace()
                            job_row.discovered_via = "remote_work"
                    counts["created" if outcome == "created" else "merged"] += 1
                    counts[result.zone.replace("accepted_suspect", "suspect")] += 1
                except Exception as exc:
                    counts["errors"] += 1
                    counts.setdefault("error_details", []).append(f"{record['title'][:24]}: {exc}")
        for step in ("extract", "dedup", "rank", "geo", "export"):
            report(step, "done")
        return counts
