"""联网搜索数据源：SearXNG 发现 → 分诊 → 抓取（含列表页一轮展开）→ 提取 → 筛选 → 入库 → 公司核查。"""
from __future__ import annotations

import time
from datetime import date

from sqlalchemy import select

from backend.core.config import ENRICH_TOP_N, SEARXNG_EMBEDDED_URL
from backend.db.database import db_scope
from backend.db.models import Job, Organization, UserPreference
from backend.discovery.base import DiscoveryError
from backend.discovery.planner import job_queries
from backend.discovery.searxng import SearxngBackend
from backend.filters import intent as intent_mod
from backend.filters.funnel import FunnelTracker
from backend.filters.pipeline import run_filters
from backend.pipeline.dedup import normalize_company
from backend.pipeline.extractor import build_record, extract_job_links
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
        status="available", rate_limit="任务级 220 次查询 / 90 页抓取",
        license_notes="本地/自托管 SearXNG 聚合，摘要即证据；不规避登录墙与验证码。",
    )

    def __init__(self):
        self._backend_url: str | None = None

    def _backend(self) -> SearxngBackend:
        if self._backend_url:
            return SearxngBackend(self._backend_url)
        with db_scope() as db:
            prefs = _get_prefs(db)
            url = (prefs.external_searxng_url or "").strip() or SEARXNG_EMBEDDED_URL
        self._backend_url = url
        return SearxngBackend(url)

    def health_check(self) -> dict:
        backend = self._backend()
        ok = backend.health()
        return {"name": self.capability.display_name, "status": "available" if ok else "offline",
                "ok": ok, "base_url": backend.base_url,
                "hint": "" if ok else "未启用：运行 scripts/enable_search.py 或在设置中填写外部 SearXNG 实例 URL"}

    def check_policy(self, url: str) -> bool:
        return True  # 抓取前由 Fetcher.can_fetch 按 robots 实时判定

    # ── 主流程 ─────────────────────────────────────────────
    def discover(self, query: dict, on_step=None, cancelled=None) -> dict:
        started = time.perf_counter()
        counts = {"discovered": 0, "fetched": 0, "snippet_only": 0, "list_pages": 0,
                  "expanded": 0, "accepted": 0, "suspect": 0, "quarantined": 0,
                  "created": 0, "merged": 0, "skipped_by_saturation": 0, "errors": 0,
                  "enriched_orgs": 0}
        budget = {"fetch": 90, "detail_per_list": 25}

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
        with db_scope() as db:
            prefs = _get_prefs(db)
            level = getattr(prefs, "filter_level", "standard") or "standard"
        queries = job_queries(intent, site_domains=registry.all_job_domains())
        counts["discovered_queries"] = len(queries)
        today = date.today()

        with db_scope() as db:
            org_name_counts: dict[str, int] = {}
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
                hits = backend.search(pq.query, engines=pq.engines, pageno=pq.pageno)
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

                # 列表页一轮展开（仅 httpx 可达的静态列表页；JS 渲染页不硬闯）
                if rule and rule.page_type == "job_list" and rule.fetcher == "httpx" and budget["fetch"] > 0:
                    ok, _why = fetcher.can_fetch(hit.url)
                    if ok:
                        fr = fetcher.fetch(hit.url)
                        budget["fetch"] -= 1
                        if fr.ok:
                            counts["list_pages"] += 1
                            for link in extract_job_links(fr.html, hit.url)[:budget["detail_per_list"]]:
                                if cancelled and cancelled():
                                    break
                                self._ingest_detail_page(link, hit.engine, intent, level, today,
                                                         fetcher, budget, counts, funnel, org_name_counts)
                            report("extract")
                            continue
                    counts["snippet_only"] += 1
                    continue

                # 详情页 / 摘要
                html, fetched = "", False
                if rule and rule.page_type == "job_page" and budget["fetch"] > 0:
                    ok, _why = fetcher.can_fetch(hit.url)
                    if ok:
                        fr = fetcher.fetch(hit.url)
                        budget["fetch"] -= 1
                        if fr.ok:
                            html, fetched = fr.html, True
                            counts["fetched"] += 1
                        else:
                            counts["snippet_only"] += 1
                    else:
                        counts["snippet_only"] += 1
                else:
                    counts["snippet_only"] += 1

                self._ingest_record(hit.url, html, fetched, hit.title, hit.snippet,
                                    hit.engine, hit.published_date, intent, level, today,
                                    counts, funnel, org_name_counts)
            report("extract", "done")

        # ── 公司核查（口碑 + 法律，榜单 Top-N）──
        report("enrich")
        try:
            from backend.providers.enrichment import enrich_organizations
            enrich_counts = enrich_organizations(backend, top_n=ENRICH_TOP_N,
                                                 on_step=on_step, cancelled=cancelled)
            counts["enriched_orgs"] = enrich_counts.get("orgs", 0)
            counts["review_docs"] = enrich_counts.get("review_docs", 0)
            counts["legal_records"] = enrich_counts.get("legal_records", 0)
        except Exception as exc:
            counts.setdefault("error_details", []).append(f"公司核查失败: {exc}")

        counts["funnel"] = {"pass_rates": funnel.pass_rates(),
                            "coverage_estimate": funnel.coverage_estimate(),
                            "unique_urls": len(funnel._seen_urls)}
        counts["elapsed_s"] = round(time.perf_counter() - started, 1)
        return counts

    # ── 记录入库（筛选 → upsert → 筛选字段回填）─────────────
    def _ingest_record(self, url, html, fetched, title, snippet, engine, published_date,
                       intent, level, today, counts, funnel, org_name_counts):
        record = build_record(html, url, {"title": title, "snippet": snippet,
                                          "engine": engine, "published_date": published_date})
        self._finalize_record(record, url, fetched, engine, intent, level, today,
                              counts, funnel, org_name_counts,
                              source_label="搜索发现")

    def _ingest_detail_page(self, url, engine, intent, level, today, fetcher, budget,
                            counts, funnel, org_name_counts):
        """列表页展开出的详情链接：robots 判定 → 抓取 → 入库。"""
        ok, _why = fetcher.can_fetch(url)
        if not ok or budget["fetch"] <= 0:
            return
        fr = fetcher.fetch(url)
        budget["fetch"] -= 1
        if not fr.ok:
            return
        counts["fetched"] += 1
        counts["expanded"] += 1
        record = build_record(fr.html, url, {"title": "", "snippet": "", "engine": engine})
        if record.get("extraction_tier") == "snippet":
            record["extraction_tier"] = "text"
        self._finalize_record(record, url, True, engine, intent, level, today,
                              counts, funnel, org_name_counts,
                              source_label="站点列表页发现")

    def _finalize_record(self, record, url, fetched, engine, intent, level, today,
                         counts, funnel, org_name_counts, source_label: str):
        site = (url.split("/")[2] if "://" in url else "").split(":")[0]
        site = site[4:] if site.startswith("www.") else site
        site_base = next((k for k in _SITE_SOURCE_TYPE if site == k or site.endswith("." + k)), site)
        record["sources"] = [{
            "provider": f"{source_label} · {engine or site}",
            "url": url,
            "source_type": _SITE_SOURCE_TYPE.get(site_base, "aggregator"),
            "access_status": "accessible" if fetched else "snippet",
            "observed_at": today.isoformat(),
            "evidence_excerpt": (record.get("description_summary") or "")[:200],
            "license_note": "robots 允许的公开详情页" if fetched else "搜索引擎索引摘要；原文未抓取",
        }]
        record["is_demo"] = False
        record["discovered_via"] = "searxng_discovery"
        record.setdefault("city", intent.city)

        existing_records, matched_org = self._existing_evidence(record)
        if matched_org:
            record["_matched_org_name"] = matched_org.get("name", "")
            record["_matched_org_uscc"] = matched_org.get("uscc")
            record["_matched_org_region"] = matched_org.get("region", "")
        result = run_filters(
            record, intent, level=level, today=today,
            same_name_orgs_count=org_name_counts.get(
                normalize_company((record.get("organization") or {}).get("name") or ""), 0),
            existing_job_records=existing_records,
        )
        for st in result.stages:
            funnel.record_stage(st["stage"], st["pass"])

        if result.zone == "quarantined" and result.rejection_reason == "missing_required":
            counts["quarantined"] += 1
            return
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
            existing = [{
                "salary_min": job.salary_min, "salary_max": job.salary_max,
                "city": job.city, "district": job.district, "experience": job.experience,
            } for job in jobs]
            return existing, matched
