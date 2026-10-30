"""国聘（iguopin.com）数据源：T1 直取层（国资/央企/事业单位招聘，官方权威）。

通道（2026-10-30 抓包定位）：`POST gp-api.iguopin.com/api/jobs/v1/list`，
httpx 直调无需认证；字段全结构化（薪资区间/有效期/城市分区/学历经验），
「成都」关键词返回 total=400。end_time 直接供状态门控判定截止。
"""
from __future__ import annotations

from datetime import date

import httpx

from backend.db.database import db_scope
from backend.filters import intent as intent_mod
from backend.filters.pipeline import run_filters
from backend.pipeline.loader import upsert_job_record
from backend.providers.base import ProviderCapability, SearchProvider

_API = "https://gp-api.iguopin.com/api/jobs/v1/list"
_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/126.0.0.0",
            "Referer": "https://www.iguopin.com/", "Origin": "https://www.iguopin.com"}

_CATEGORY_MAP = [
    (("技术", "研发", "软件", "信息", "数据", "算法"), "互联网/软件"),
    (("金融", "会计", "审计", "投资"), "金融/财务"),
    (("教师", "教育", "科研"), "教育/科研"),
    (("医师", "医", "护理", "药"), "医疗健康"),
    (("机械", "电气", "制造", "工艺", "质量"), "制造/工业"),
    (("建筑", "工程", "造价", "勘察"), "建筑/地产"),
    (("设计", "美术", "创意"), "设计创意"),
    (("市场", "销售", "营销", "商务"), "销售/市场"),
    (("人事", "人力", "行政", "法务", "党务"), "职能/法律/人力"),
    (("物流", "仓储", "供应链", "采购"), "供应链/物流"),
]


def map_category(category_cn: str) -> str:
    text = (category_cn or "")
    for keys, cat in _CATEGORY_MAP:
        if any(k in text for k in keys):
            return cat
    return "其他行业"


def fetch_jobs(keyword: str, pages: int = 2, page_size: int = 30,
               client: httpx.Client | None = None) -> list[dict]:
    own = client is None
    c = client or httpx.Client(timeout=15, headers=_HEADERS, trust_env=False)
    items: list[dict] = []
    try:
        for page in range(1, pages + 1):
            resp = c.post(_API, json={"page": page, "page_size": page_size, "keyword": keyword})
            resp.raise_for_status()
            data = resp.json().get("data") or {}
            batch = data.get("list") or []
            items.extend(batch)
            if len(batch) < page_size or len(items) >= (data.get("total") or 0):
                break
        return items
    finally:
        if own:
            c.close()


def to_record(job: dict, today: date) -> dict:
    district = (job.get("district_list") or [{}])[0]
    area_cn = district.get("area_cn") or ""  # 形如 成都-郫都区
    city, _, dist = area_cn.partition("-")
    is_neg = bool(job.get("is_negotiable"))
    min_wage = job.get("min_wage") or 0
    max_wage = job.get("max_wage") or 0
    unit = job.get("wage_unit_cn") or ""
    # 元/月 → 千/月（JobMap 口径为 K）
    if "元" in unit and max_wage >= 1000:
        min_wage, max_wage = round(min_wage / 1000, 1), round(max_wage / 1000, 1)
    job_id = job.get("job_id") or ""
    return {
        "organization": {
            "name": job.get("company_name") or "国聘企业",
            "type": "enterprise" if "集团" in (job.get("company_name") or "")
                    or "有限" in (job.get("company_name") or "") else "institute",
            "industry": "",
        },
        "title": (job.get("job_name") or "")[:100],
        "description_summary": f"{job.get('company_name')} {job.get('recruitment_type_cn', '')}："
                               f"{job.get('job_name')}（招 {job.get('amount', '?')} 人）",
        "city": city,
        "district": dist,
        "salary_min": None if is_neg or not min_wage else float(min_wage),
        "salary_max": None if is_neg or not max_wage else float(max_wage),
        "salary_months": job.get("months") or None,
        "experience": job.get("experience_cn") or "不限",
        "education": job.get("education_cn") or "",
        "job_category": map_category(job.get("category_cn")),
        "industry": job.get("category_cn", "")[:30],
        "keywords": [k for k in (job.get("category_cn") or "").split("/") if k][:4],
        "published_at": (job.get("start_time") or "")[:10] or None,
        "valid_through": (job.get("end_time") or "")[:10] or None,
        "last_checked": today.isoformat(),
        "status_hint": None,
        "extraction_tier": "jsonld",
        "sources": [{
            "provider": "国聘 iguopin.com",
            "url": f"https://www.iguopin.com/job?id={job_id}",
            "source_type": "gov_public",
            "access_status": "accessible",
            "observed_at": today.isoformat(),
            "evidence_excerpt": f"国聘官方接口记录：{job.get('job_name')}（{job.get('company_name')}）",
            "license_note": "国聘官方公开招聘接口（T1），保留原文链接",
        }],
        "is_demo": False,
        "discovered_via": "iguopin",
    }


class IguopinProvider(SearchProvider):
    capability = ProviderCapability(
        name="iguopin", display_name="国聘（央企/国资直取）", source_type="gov_public",
        status="available", rate_limit="2 页 × 30 条/任务",
        license_notes="国聘官方公开职位接口（POST /api/jobs/v1/list，无需认证）。",
    )

    def check_policy(self, url: str) -> bool:
        return "iguopin.com" in url

    def health_check(self) -> dict:
        try:
            jobs = fetch_jobs("招聘", pages=1, page_size=5)
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
        keyword = " ".join(x for x in [keywords, query.get("city") or ""] if x).strip() or "招聘"
        try:
            jobs = fetch_jobs(keyword)
        except Exception as exc:
            counts["errors"] = 1
            counts["error_details"] = [f"国聘接口失败: {exc}"]
            return counts
        counts["discovered"] = len(jobs)
        report("discover", "done")
        report("extract")

        today = date.today()
        for job in jobs:
            if cancelled and cancelled():
                break
            if not (job.get("job_name") and job.get("company_name")):
                continue
            record = to_record(job, today)
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
                        job_row.discovered_via = "iguopin"
                counts["created" if outcome == "created" else "merged"] += 1
                counts[result.zone.replace("accepted_suspect", "suspect")] += 1
            except Exception as exc:
                counts["errors"] += 1
                counts.setdefault("error_details", []).append(f"{record['title'][:24]}: {exc}")
        for step in ("extract", "dedup", "rank", "geo", "export"):
            report(step, "done")
        return counts
