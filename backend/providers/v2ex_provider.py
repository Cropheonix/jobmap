"""V2EX 酷工作节点数据源：T1 直取层（互联网/远程/开发者岗位）。

通道：V2EX 官方公开 API `GET /api/topics/show.json?node_name=jobs`，
无需认证、无风控（2026-10-30 POC 实测），每次返回最新约 10 条招聘帖。
本源不依赖搜索引擎配额，是引擎限流时的独立补充通道。
"""
from __future__ import annotations

import re
from datetime import date, datetime

import httpx

from backend.db.database import db_scope
from backend.filters import intent as intent_mod
from backend.filters.pipeline import run_filters
from backend.pipeline.extractor import extract_by_regex
from backend.pipeline.loader import upsert_job_record
from backend.providers.base import ProviderCapability, SearchProvider

_API_URL = "https://www.v2ex.com/api/topics/show.json"
_HEADERS = {"User-Agent": "JobMap/1.0 (+local personal research)"}
_ORG_RE = re.compile(r"(.{2,25}?)(?:有限公司|股份有限公司|有限责任公司|集团公司|科技公司|网络公司|事务所|工作室|实验室)")


def parse_topic(topic: dict) -> dict:
    """V2EX 招聘帖 → 规范化记录。"""
    title = (topic.get("title") or "").strip()
    content = re.sub(r"<[^>]+>", " ", topic.get("content") or "")[:800]
    m = re.search(r"[【\[]([\u4e00-\u9fa5]{2,4})[】\]]", title)
    city = m.group(1) if m else ""
    org_m = _ORG_RE.search(title + " " + content)
    org_name = (org_m.group(1) + "公司") if org_m and org_m.group(1) else ""
    fields = extract_by_regex(f"{title}\n{content}")
    created = topic.get("created")
    published = None
    if isinstance(created, (int, float)):
        try:
            published = datetime.fromtimestamp(int(created)).date().isoformat()
        except (ValueError, OSError):
            pass
    return {
        "organization": {"name": org_name or "V2EX 招聘方", "type": "company", "industry": "互联网"},
        "title": title.split("|")[0].strip()[:80] or title[:80],
        "description_summary": content,
        "city": city,
        "salary_min": fields.get("salary_min"),
        "salary_max": fields.get("salary_max"),
        "salary_months": fields.get("salary_months"),
        "published_at": published,
        "job_category": "互联网/软件",
        "industry": "互联网",
        "keywords": [k for k in re.split(r"[·|｜,，/、\s]+", title) if 2 <= len(k) <= 12][:8],
        "extraction_tier": "dom",
        "sources": [{
            "provider": "V2EX 酷工作",
            "url": topic.get("url") or f"https://www.v2ex.com/t/{topic.get('id')}",
            "source_type": "vertical_site",
            "access_status": "accessible",
            "observed_at": date.today().isoformat(),
            "evidence_excerpt": title[:200],
            "license_note": "V2EX 公开节点 API（官方接口），保留原文链接",
        }],
        "is_demo": False,
        "discovered_via": "v2ex",
    }


class V2exProvider(SearchProvider):
    capability = ProviderCapability(
        name="v2ex", display_name="V2EX 酷工作（垂直站直取）", source_type="vertical_site",
        status="available", rate_limit="官方 API，单次/任务",
        license_notes="V2EX 官方公开 API；保留原文链接与社区归属。",
    )

    def check_policy(self, url: str) -> bool:
        return "v2ex.com" in url

    def health_check(self) -> dict:
        try:
            r = httpx.get(_API_URL, params={"node_name": "jobs"}, headers=_HEADERS,
                          timeout=12, trust_env=False)
            return {"name": self.capability.display_name, "status": "available",
                    "ok": r.status_code == 200, "records": len(r.json()) if r.status_code == 200 else 0}
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
        try:
            r = httpx.get(_API_URL, params={"node_name": "jobs"}, headers=_HEADERS,
                          timeout=15, trust_env=False)
            r.raise_for_status()
            topics = r.json()
        except Exception as exc:
            counts["errors"] = 1
            counts["error_details"] = [f"V2EX API 失败: {exc}"]
            return counts
        counts["discovered"] = len(topics)
        report("discover", "done")
        report("extract")

        today = date.today()
        for topic in topics:
            if cancelled and cancelled():
                break
            record = parse_topic(topic)
            if not record["organization"]["name"]:
                continue
            result = run_filters(record, intent, today=today)
            if result.zone == "quarantined" and result.rejection_reason in ("missing_required", "qa_page"):
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
                        job_row.discovered_via = "v2ex"
                counts["created" if outcome == "created" else "merged"] += 1
                counts[result.zone.replace("accepted_suspect", "suspect")] += 1
            except Exception as exc:
                counts["errors"] += 1
                counts.setdefault("error_details", []).append(f"{record['title'][:24]}: {exc}")
        for step in ("extract", "dedup", "rank", "geo", "export"):
            report(step, "done")
        return counts
