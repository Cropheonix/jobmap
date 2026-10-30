"""金融招聘网（jrzp.com）数据源：T1 直取层（银行/央企/事业/教师公告类）。

通道（2026-10-30 POC）：分类列表页 SSR 公开（每页约 23 条公告），
详情页公开（40KB+）。公告型数据（一条=一机构一批岗位），标题含机构与人数。
"""
from __future__ import annotations

import re
import time as _time
from datetime import date
from urllib.parse import urljoin

import httpx

from backend.db.database import db_scope
from backend.filters import intent as intent_mod
from backend.filters.pipeline import run_filters
from backend.pipeline.loader import upsert_job_record
from backend.providers.base import ProviderCapability, SearchProvider

_BASE = "https://www.jrzp.com/"
_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/126.0.0.0"}
# 频道：id → (名称, 类别映射)
_CHANNELS = {
    "8": ("银行招聘", "金融/财务"),
    "10": ("企业招聘", "其他行业"),
    "1": ("事业单位", "职能/法律/人力"),
    "4": ("央企招聘", "其他行业"),
    "5": ("教师招聘", "教育/科研"),
    "6": ("医院招聘", "医疗健康"),
}
_ITEM_RE = re.compile(
    r'<a[^>]+href="(https?://www\.jrzp\.com/shiyezhaopin/[0-9A-Fa-f]{8,}\.shtml)"[^>]*>'
    r'(?:\s*<[^>]+>)*([^<]{4,80})</a>')
_ORG_RE = re.compile(r"([\u4e00-\u9fa5]{2,18}?(?:银行|集团|公司|学校|医院|中心|管理局|事务所|证券|基金))")


def pick_channels(intent) -> list[str]:
    """按查询类别优先选择频道（金融→银行+企业；医疗→医院；教育→教师；默认前 3）。"""
    cat = intent.category or ""
    prefer = {"金融/财务": ["8", "10"], "教育/科研": ["5", "1"], "医疗健康": ["6"],
              "互联网/软件": ["10"], "职能/法律/人力": ["1", "10"]}
    return prefer.get(cat, ["8", "10", "1"])[:3]


def parse_org(title: str) -> str:
    m = _ORG_RE.search(title)
    if not m:
        return ""
    return m.group(1).lstrip("0123456789年月季度")


class JrzpProvider(SearchProvider):
    capability = ProviderCapability(
        name="jrzp", display_name="金融招聘网（公告直取）", source_type="vertical_site",
        status="available", rate_limit="≤3 频道页 + 8 详情页/任务，间隔 2s",
        license_notes="jrzp.com 公开招聘公告列表（T1），保留原文链接与归属。",
    )

    def check_policy(self, url: str) -> bool:
        return "jrzp.com" in url

    def health_check(self) -> dict:
        try:
            with httpx.Client(timeout=12, headers=_HEADERS, follow_redirects=True) as c:
                r = c.get(_BASE + "shiyezhaopin/zhaopin_8.shtml")
                n = len(set(u for u, _ in _ITEM_RE.findall(r.text)))
            return {"name": self.capability.display_name, "status": "available", "ok": n > 0, "records": n}
        except Exception as exc:
            return {"name": self.capability.display_name, "status": "offline", "ok": False,
                    "error": str(exc)[:200]}

    def discover(self, query: dict, on_step=None, cancelled=None) -> dict:
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
        entries: list[tuple[str, str, str]] = []  # (url, title, category)
        with httpx.Client(timeout=15, headers=_HEADERS, follow_redirects=True) as c:
            for chan in pick_channels(intent):
                if cancelled and cancelled():
                    break
                try:
                    r = c.get(f"{_BASE}shiyezhaopin/zhaopin_{chan}.shtml")
                    seen_here: set[str] = set()
                    for url, title in _ITEM_RE.findall(r.text):
                        url = url.split("?")[0]
                        if url not in seen_here and title.strip():
                            seen_here.add(url)
                            entries.append((url, title.strip(), _CHANNELS[chan][1]))
                except httpx.HTTPError as exc:
                    counts.setdefault("error_details", []).append(f"频道{chan}: {exc}")
                _time.sleep(2.0)
        # 去重（跨频道同 URL）
        uniq: dict[str, tuple[str, str]] = {}
        for url, title, cat in entries:
            uniq.setdefault(url, (title, cat))
        counts["discovered"] = len(uniq)
        report("discover", "done")
        report("extract")

        with httpx.Client(timeout=15, headers=_HEADERS, follow_redirects=True) as c:
            for i, (url, (title, cat)) in enumerate(uniq.items()):
                if cancelled and cancelled():
                    break
                if i >= 30:
                    break
                summary = title
                if i < 8:  # 少量详情页增强（日期/摘要）
                    _time.sleep(2.0)
                    try:
                        resp = c.get(url)
                        if resp.status_code == 200:
                            counts["fetched"] += 1
                            text = re.sub(r"<[^>]+>", " ", resp.text)
                            m = re.search(r"(20\d{2}-\d{2}-\d{2})", text)
                            if m:
                                summary = f"{title}（发布 {m.group(1)}）"
                    except httpx.HTTPError:
                        pass
                org = parse_org(title)
                record = {
                    "organization": {"name": org or "jrzp 公告", "type": "institute"
                                     if "事业" in cat or "管理局" in title else "enterprise",
                                     "industry": cat},
                    "title": title[:100],
                    "description_summary": summary[:600],
                    "city": query.get("city") or "",
                    "job_category": cat,
                    "industry": cat,
                    "keywords": [k for k in re.split(r"[，、/（）()\s]+", title) if 2 <= len(k) <= 12][:6],
                    "published_at": None,
                    "last_checked": today.isoformat(),
                    "extraction_tier": "dom" if i < 8 else "text",
                    "sources": [{
                        "provider": "金融招聘网 jrzp.com",
                        "url": url,
                        "source_type": "vertical_site",
                        "access_status": "accessible",
                        "observed_at": today.isoformat(),
                        "evidence_excerpt": title[:200],
                        "license_note": "jrzp 公开公告列表，保留原文链接",
                    }],
                    "is_demo": False,
                    "discovered_via": "jrzp",
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
                            job_row.discovered_via = "jrzp"
                    counts["created" if outcome == "created" else "merged"] += 1
                    counts[result.zone.replace("accepted_suspect", "suspect")] += 1
                except Exception as exc:
                    counts["errors"] += 1
                    counts.setdefault("error_details", []).append(f"{title[:24]}: {exc}")
        for step in ("extract", "dedup", "rank", "geo", "export"):
            report(step, "done")
        return counts
