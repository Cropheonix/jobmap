"""谷德设计（gooood.cn）垂直站数据源：T1 直取层，设计领域批量职位。

数据通道（2026-10-30 抓包定位）：Nuxt3 `_payload.json` 接口，httpx 直达、无风控，
每页约 170 条职位，字段含发布时间/标题（（城市）公司 - 岗位列表）/原文链接/城市。
本源不依赖 SearXNG 引擎配额，是引擎限流时的独立补充通道。
"""
from __future__ import annotations

import re
from datetime import date

import httpx

from backend.db.database import db_scope
from backend.filters import intent as intent_mod
from backend.filters.pipeline import run_filters
from backend.pipeline.loader import upsert_job_record
from backend.providers.base import ProviderCapability, SearchProvider

_PAYLOAD_URL = "https://www.gooood.cn/category/jobs/_payload.json"
_HEADERS = {"User-Agent": "JobMap/1.0 (+local personal research)",
            "Referer": "https://www.gooood.cn/category/jobs/"}
_WRAPPERS = ("ShallowReactive", "Reactive", "Ref", "Map", "Set", "ShallowRef")
# 标题格式：「（城市）公司 - 岗位1 / 岗位2（暂招满）」；分隔符兼容 -、–、—、－
_DASH = "－\\-—–"
_TITLE_RE = re.compile(rf"^（(?P<city>[^）]{{1,8}})）\s*(?P<org>[^{_DASH}]{{2,40}}?)\s*[{_DASH}]\s*(?P<roles>.+)$")
_TITLE_RE2 = re.compile(rf"^(?P<org>[^{_DASH}（]{{2,40}}?)\s*[{_DASH}]\s*(?P<roles>.+)$")


def _deref(payload: list, obj, depth: int = 0):
    """Nuxt(devalue) payload 解引用：dict 值/list 单元素中的整数为池索引。"""
    if depth > 60 or not isinstance(obj, (list, dict)):
        return obj
    if isinstance(obj, list):
        if len(obj) == 2 and isinstance(obj[0], str) and obj[0] in _WRAPPERS and isinstance(obj[1], int):
            return _deref(payload, payload[obj[1]], depth + 1)
        if len(obj) <= 3 and all(isinstance(x, int) for x in obj):
            # 索引数组（如 cityinfo: [13]）
            out = [_deref(payload, payload[x], depth + 1) for x in obj]
            return out if len(out) > 1 else out[0]
        return [_deref(payload, x, depth + 1) for x in obj]
    return {k: (_deref(payload, payload[v], depth + 1)
                if isinstance(v, int) and 0 <= v < len(payload) else _deref(payload, v, depth + 1))
            for k, v in obj.items()}


def decode_jobs(payload: list) -> list[dict]:
    """还原为职位字典列表。"""
    top = _deref(payload, payload[1])
    index_arr = top.get("jobs-production-index") if isinstance(top, dict) else None
    if isinstance(index_arr, dict):  # 单元素索引数组被 deref 折叠为对象
        return [index_arr]
    if not isinstance(index_arr, list):
        return []
    jobs = []
    for i in index_arr:
        if isinstance(i, int) and 0 <= i < len(payload):
            try:
                job = _deref(payload, payload[i])
                if isinstance(job, dict):
                    jobs.append(job)
            except Exception:
                continue
    return jobs


def parse_title(title: str) -> dict:
    """解析「（城市）公司 - 岗位列表」格式。"""
    t = (title or "").strip()
    city, org, roles = "", "", t
    m = _TITLE_RE.match(t)
    if m:
        city, org, roles = m.group("city").strip(), m.group("org").strip(), m.group("roles").strip()
    else:
        m2 = _TITLE_RE2.match(t)
        if m2:
            org, roles = m2.group("org").strip(), m2.group("roles").strip()
    closed = any(marker in t for marker in ("暂招满", "已招满", "停止招聘"))
    return {"city": city, "org": org, "roles": roles, "closed": closed}


def fetch_payload(client: httpx.Client | None = None) -> list[dict]:
    own = client is None
    c = client or httpx.Client(timeout=15, trust_env=False, headers=_HEADERS, follow_redirects=True)
    try:
        resp = c.get(_PAYLOAD_URL)
        resp.raise_for_status()
        return decode_jobs(resp.json())
    finally:
        if own:
            c.close()


class GoooodProvider(SearchProvider):
    capability = ProviderCapability(
        name="gooood", display_name="谷德设计（垂直站直取）", source_type="vertical_site",
        status="available", rate_limit="单次 payload 请求/任务",
        license_notes="gooood.cn 公开招聘聚合页（企业投稿发布），T1 直取层；保留原文链接与归属。",
    )

    def check_policy(self, url: str) -> bool:
        return "gooood.cn" in url

    def health_check(self) -> dict:
        try:
            jobs = fetch_payload()
            return {"name": self.capability.display_name, "status": "available",
                    "ok": bool(jobs), "records": len(jobs)}
        except Exception as exc:
            return {"name": self.capability.display_name, "status": "offline", "ok": False,
                    "error": str(exc)[:200]}

    def discover(self, query: dict, on_step=None, cancelled=None) -> dict:
        counts = {"discovered": 0, "accepted": 0, "suspect": 0, "quarantined": 0,
                  "created": 0, "merged": 0, "errors": 0}

        def report(step="dedup", state="running"):
            if on_step:
                on_step({"step": step, "state": state, "counts": dict(counts)})

        report("discover")
        keywords = " ".join(query.get("keywords") or [])
        intent = intent_mod.parse_intent(f"{keywords} {query.get('city') or ''}".strip(),
                                         intent_mod.load_synonyms())
        want_city = (query.get("city") or "").strip()
        try:
            jobs = fetch_payload()
        except Exception as exc:
            counts["errors"] = 1
            counts["error_details"] = [f"谷德 payload 获取失败: {exc}"]
            return counts
        counts["discovered"] = len(jobs)
        report("discover", "done")
        report("extract")

        today = date.today()
        for job in jobs:
            if cancelled and cancelled():
                break
            title = ((job.get("title") or {}).get("rendered")
                     if isinstance(job.get("title"), dict) else str(job.get("title") or ""))
            parsed = parse_title(title)
            link = (job.get("linkurl") or "").strip()
            if not link:
                continue
            city = parsed["city"]
            if want_city and city and city != want_city:
                continue  # 明确标了其他城市的跳过；无城市标记的保留（中性）
            roles = parsed["roles"] or title
            record = {
                "organization": {"name": parsed["org"] or "谷德招聘", "type": "studio"
                                 if "事务所" in parsed["org"] or "studio" in parsed["org"].lower() else "company",
                                 "industry": "设计"},
                "title": (roles.split("/")[0].strip()[:60] or title[:60]),
                "description_summary": f"{parsed['org']} 招聘：{roles}"[:600],
                "city": city or want_city,
                "published_at": (job.get("date") or "")[:10] or None,
                "last_checked": today.isoformat(),
                "status_hint": "已招满" if parsed["closed"] else None,
                "job_category": "设计创意",
                "industry": "设计",
                "keywords": re.split(r"[/、,，\s]+", roles)[:8],
                "extraction_tier": "dom",
                "sources": [{
                    "provider": "谷德设计 gooood.cn",
                    "url": link,
                    "source_type": "vertical_site",
                    "access_status": "accessible",
                    "observed_at": today.isoformat(),
                    "evidence_excerpt": title[:200],
                    "license_note": "谷德公开招聘聚合页（企业投稿），保留原文链接与归属",
                }],
                "is_demo": False,
                "discovered_via": "gooood",
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
                        job_row.discovered_via = "gooood"
                counts["created" if outcome == "created" else "merged"] += 1
                counts[result.zone.replace("accepted_suspect", "suspect")] += 1
            except Exception as exc:
                counts["errors"] += 1
                counts.setdefault("error_details", []).append(f"{title[:30]}: {exc}")
        report("extract", "done")
        report("dedup", "done")
        report("rank", "done")
        report("geo", "done")
        report("export", "done")
        return counts
