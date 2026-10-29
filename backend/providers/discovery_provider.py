"""联网搜索数据源：SearXNG 发现 → 分诊 → 抓取 → 提取 → 筛选 → 入库（M2 主链路）。"""
from __future__ import annotations

import time
from datetime import date

from sqlalchemy import select

from backend.core.config import SEARXNG_EMBEDDED_URL
from backend.db.database import db_scope
from backend.db.models import Job, Organization, UserPreference
from backend.discovery.base import DiscoveryError
from backend.discovery.planner import job_queries
from backend.discovery.searxng import SearxngBackend
from backend.filters import intent as intent_mod
from backend.filters.funnel import FunnelTracker
from backend.filters.pipeline import run_filters
from backend.pipeline.dedup import classify, normalize_company
from backend.pipeline.extractor import build_record
from backend.pipeline.fetcher import Fetcher
from backend.pipeline.loader import upsert_job_record
from backend.pipeline.site_registry import SiteRegistry
from backend.providers.base import ProviderCapability, SearchProvider

_SPAM_WORDS = ("日结", "加微信", "加v", "点击申请", "立即提现", "高薪兼职")

_SITE_SOURCE_TYPE = {
    "zhipin.com": "platform_public", "zhaopin.com": "platform_public",
    "51job.com": "platform_public", "liepin.com": "platform_public", "yupao.com": "platform_public",
}


def _get_prefs(db) -> UserPreference:
    prefs = db.get(UserPreference, 1)
    return prefs if prefs else UserPreference(id=1)


class SearxngDiscoveryProvider(SearchProvider):
    capability = ProviderCapability(
        name="searxng_discovery", display_name="联网搜索（SearXNG）", source_type="aggregator",
        status="available", rate_limit="任务级 200 次查询 / 40 页抓取",
        license_notes="本地/自托管 SearXNG 聚合，摘要即证据；不规避登录墙与验证码。",
    )

    def _backend(self) -> SearxngBackend:
        with db_scope() as db:
            prefs = _get_prefs(db)
            url = (prefs.external_searxng_url or "").strip() or SEARXNG_EMBEDDED_URL
        return SearxngBackend(url)

    def health_check(self) -> dict:
        backend = self._backend()
        ok = backend.health()
        return {"name": self.capability.display_name, "status": "available" if ok else "offline",
                "ok": ok, "base_url": backend.base_url,
                "hint": "" if ok else "未启用：运行 scripts/enable_search.py 或在设置中填写外部 SearXNG 实例 URL"}

    def check_policy(self, url: str) -> bool:
        return True  # 抓取前由 Fetcher.can_fetch 按 robots 实时判定

    def discover(self, query: dict, on_step=None, cancelled=None) -> dict:
        started = time.perf_counter()
        counts = {"discovered": 0, "fetched": 0, "snippet_only": 0,
                  "accepted": 0, "suspect": 0, "quarantined": 0,
                  "created": 0, "merged": 0, "skipped_by_saturation": 0, "errors": 0}

        backend = self._backend()
        if not backend.health():
            raise DiscoveryError("搜索通道未启用：请运行 scripts/enable_search.py，或在设置中配置外部 SearXNG 实例")

        def report(step="dedup", state="running"):
            if on_step:
                on_step({"step": step, "state": state, "counts": dict(counts)})

        report("discover")
        keywords = " ".join(query.get("keywords") or [])
        intent = intent_mod.parse_intent(f"{keywords} {query.get('city') or ''}".strip(),
                                         intent_mod.load_synonyms())
        registry = SiteRegistry()
        fetcher = Fetcher()
        funnel = FunnelTracker()
        thresholds_level = "standard"
        with db_scope() as db:
            prefs = _get_prefs(db)
            thresholds_level = getattr(prefs, "filter_level", "standard") or "standard"
        queries = job_queries(intent, site_domains=registry.all_job_domains())
        counts["discovered_queries"] = len(queries)

        fetch_budget = 40
        today = date.today()
        org_name_counts: dict[str, int] = {}
        with db_scope() as db:
            for org in db.execute(select(Organization)).scalars():
                key = normalize_company(org.canonical_name)
                org_name_counts[key] = org_name_counts.get(key, 0) + 1

        for pq in queries:
            if cancelled and cancelled():
                report("dedup", "cancelled")
                counts["cancelled"] = True
                return counts
            if funnel.saturated() and pq.priority >= 2:
                counts["skipped_by_saturation"] += 1
                continue
            try:
                hits = backend.search(pq.query, engines=pq.engines)
            except DiscoveryError as exc:
                counts["errors"] += 1
                counts.setdefault("error_details", []).append(str(exc))
                continue
            by_engine: dict[str, list[str]] = {}
            for hit in hits:
                by_engine.setdefault(hit.engine or "?", []).append(hit.url)
            funnel.add_batch([h.url for h in hits])
            for engine, urls in by_engine.items():
                funnel.register_engine_results(engine, urls)
            counts["discovered"] += len(hits)
            report("discover")

            report("extract")
            for hit in hits:
                if cancelled and cancelled():
                    break
                blob = (hit.title + hit.snippet).lower()
                if any(w in blob for w in _SPAM_WORDS):
                    funnel.record_stage("S0", False)
                    counts["quarantined"] += 1
                    continue
                funnel.record_stage("S0", True)

                rule, _entry = registry.triage(hit.url)
                html, fetched = "", False
                if (rule and rule.page_type == "job_page" and fetch_budget > 0):
                    ok, _why = fetcher.can_fetch(hit.url)
                    if ok:
                        fr = fetcher.fetch(hit.url)
                        fetch_budget -= 1
                        if fr.ok:
                            html, fetched = fr.html, True
                            counts["fetched"] += 1
                        else:
                            counts["snippet_only"] += 1
                    else:
                        counts["snippet_only"] += 1
                else:
                    counts["snippet_only"] += 1

                record = build_record(html, hit.url, {
                    "title": hit.title, "snippet": hit.snippet,
                    "engine": hit.engine, "published_date": hit.published_date,
                })
                site = hit.site.split(":")[0]
                record["sources"] = [{
                    "provider": f"搜索发现 · {hit.engine or site}",
                    "url": hit.url,
                    "source_type": _SITE_SOURCE_TYPE.get(site, "aggregator"),
                    "access_status": "accessible" if fetched else "snippet",
                    "observed_at": today.isoformat(),
                    "evidence_excerpt": (hit.snippet or hit.title)[:200],
                    "license_note": "搜索引擎索引摘要；原文未抓取" if not fetched else "robots 允许的公开详情页",
                }]
                record["is_demo"] = False
                record["discovered_via"] = "searxng_discovery"
                record.setdefault("city", query.get("city") or "")

                existing_records, matched_org = self._existing_evidence(record)
                if matched_org:
                    record["_matched_org_name"] = matched_org.get("name", "")
                    record["_matched_org_uscc"] = matched_org.get("uscc")
                    record["_matched_org_region"] = matched_org.get("region", "")
                result = run_filters(
                    record, intent, level=thresholds_level, today=today,
                    same_name_orgs_count=org_name_counts.get(
                        normalize_company((record.get("organization") or {}).get("name") or ""), 0),
                    existing_job_records=existing_records,
                )
                for st in result.stages:
                    funnel.record_stage(st["stage"], st["pass"])

                if result.zone == "quarantined" and result.rejection_reason == "missing_required":
                    counts["quarantined"] += 1
                    continue
                try:
                    with db_scope() as db:
                        outcome, job, _why = upsert_job_record(db, record)
                        if job is not None:
                            job.filter_zone = result.zone
                            job.confidence = result.confidence
                            job.rejection_reason = result.rejection_reason or ""
                            job.filter_trace = result.to_trace()
                            job.discovered_via = "searxng_discovery"
                    counts["created" if outcome == "created" else "merged"] += 1
                    counts[result.zone.replace("accepted_suspect", "suspect")] += 1
                except Exception as exc:  # 单条失败不阻断
                    counts["errors"] += 1
                    counts.setdefault("error_details", []).append(f"{record.get('title')}: {exc}")
            report("extract", "done")

        counts["funnel"] = {"pass_rates": funnel.pass_rates(),
                            "coverage_estimate": funnel.coverage_estimate(),
                            "unique_urls": len(funnel._seen_urls)}
        counts["elapsed_s"] = round(time.perf_counter() - started, 1)
        return counts

    @staticmethod
    def _existing_evidence(record: dict) -> tuple[list[dict], dict | None]:
        """找同主体/同职位的既有记录（供 S5 一致性比较）与已匹配机构。"""
        org_name = (record.get("organization") or {}).get("name") or ""
        title = record.get("title") or ""
        city = record.get("city") or ""
        if not org_name:
            return [], None
        with db_scope() as db:
            org = db.execute(select(Organization).where(
                Organization.canonical_name == org_name)).scalar_one_or_none()
            matched = {"name": org.canonical_name, "uscc": org.uscc,
                       "region": org.registered_region} if org else None
            if not org:
                return [], matched
            jobs = db.execute(select(Job).where(Job.organization_id == org.id,
                                                Job.city == city)).scalars().all()
            verdicts = [classify(canonical_urls=set(), provider_post_ids=set(),
                                 company_norm=normalize_company(org_name), title=title,
                                 city=city, district=record.get("district") or "",
                                 existing=jobs)]
            existing = []
            for job in jobs:
                existing.append({
                    "salary_min": job.salary_min, "salary_max": job.salary_max,
                    "city": job.city, "district": job.district, "experience": job.experience,
                })
            return existing, matched
