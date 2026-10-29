"""公司核查管线（M2.3）：口碑（知乎/小红书/贴吧）+ 法律（信用/执行/欠薪）证据采集与信号聚合。"""
from __future__ import annotations

from datetime import date
from urllib.parse import urlparse

from sqlalchemy import select

from backend.core.config import ENRICH_TOP_N
from backend.db.database import db_scope
from backend.db.models import Job, LegalRecord, Organization, ReviewEvidence
from backend.discovery.planner import legal_queries, review_queries
from backend.discovery.searxng import SearxngBackend
from backend.pipeline.extractor import extract_by_regex
from backend.signals.service import aggregate_legal, aggregate_review, analyze_review, classify_legal, review_confidence

_REVIEW_SITE = {"zhihu.com": "zhihu", "tieba.baidu.com": "tieba", "xiaohongshu.com": "xhs",
                "www.zhihu.com": "zhihu", "m.zhihu.com": "zhihu"}


def _site_of(url: str) -> str:
    host = (urlparse(url).netloc or "").lower()
    host = host[4:] if host.startswith("www.") else host
    return _REVIEW_SITE.get(host, "other")


def enrich_organizations(backend: SearxngBackend, org_ids: list[int] | None = None,
                         top_n: int = ENRICH_TOP_N, on_step=None, cancelled=None) -> dict:
    """对指定（或榜单 Top-N）机构执行口碑+法律发现，写入证据表并返回信号。"""
    counts = {"orgs": 0, "review_docs": 0, "legal_records": 0, "queries": 0}
    today = date.today()

    def report(state="running"):
        if on_step:
            on_step({"step": "enrich", "state": state, "counts": dict(counts)})

    report()
    with db_scope() as db:
        if org_ids is None:
            jobs = db.execute(select(Job).where(
                Job.filter_zone.in_(["accepted", "accepted_suspect"]),
                Job.status.in_(["active", "unverified"]))).scalars().all()
            tally: dict[int, int] = {}
            for j in jobs:
                tally[j.organization_id] = tally.get(j.organization_id, 0) + 1
            org_ids = [oid for oid, _ in sorted(tally.items(), key=lambda x: -x[1])[:top_n]]
        orgs = [(o.id, o.canonical_name, o.registered_region or "") for o in
                db.execute(select(Organization).where(Organization.id.in_(org_ids or [0]))).scalars()]

    for oid, name, region in orgs:
        if cancelled and cancelled():
            report("cancelled")
            return counts
        # ── 口碑：3 站点 × 1 组词（预算考虑合并「怎么样 避雷」） ──
        for pq in review_queries(name)[:3]:
            try:
                hits = backend.search(pq.query, engines=pq.engines)
                counts["queries"] += 1
            except Exception:
                continue
            for hit in hits[:8]:
                text = f"{hit.title} {hit.snippet}"
                conf = review_confidence(name, text)
                sentiment, kws = analyze_review(text)
                with db_scope() as db:
                    exists = db.execute(select(ReviewEvidence).where(
                        ReviewEvidence.organization_id == oid,
                        ReviewEvidence.url == hit.url)).scalar_one_or_none()
                    if exists:
                        continue
                    db.add(ReviewEvidence(
                        organization_id=oid, source=_site_of(hit.url), url=hit.url,
                        title=hit.title[:500], snippet=(hit.snippet or "")[:1000],
                        sentiment=sentiment, hit_keywords=kws,
                        match_confidence=conf, observed_at=today))
                    counts["review_docs"] += 1
        # ── 法律：被执行/仲裁/处罚 三类查询 ──
        for pq in legal_queries(name, region):
            if cancelled and cancelled():
                break
            try:
                hits = backend.search(pq.query, engines=pq.engines)
                counts["queries"] += 1
            except Exception:
                continue
            for hit in hits[:6]:
                text = f"{hit.title} {hit.snippet}"
                if name[:4] not in text:      # 主体不匹配的摘要不采
                    continue
                rtype = classify_legal(text)
                raw_date = extract_by_regex(text).get("published_at")
                filing = None
                if raw_date:
                    from datetime import datetime as _dt
                    try:
                        filing = _dt.strptime(raw_date[:10], "%Y-%m-%d").date()
                    except ValueError:
                        filing = None
                with db_scope() as db:
                    exists = db.execute(select(LegalRecord).where(
                        LegalRecord.organization_id == oid,
                        LegalRecord.url == hit.url)).scalar_one_or_none()
                    if exists:
                        continue
                    db.add(LegalRecord(
                        organization_id=oid, source="discovery", record_type=rtype,
                        title=hit.title[:500], summary=(hit.snippet or "")[:1000],
                        filing_date=filing, match_confidence=0.75, url=hit.url,
                        observed_at=today))
                    counts["legal_records"] += 1
        counts["orgs"] += 1
        report()

    report("done")
    return counts


def org_signals(oid: int) -> dict:
    """聚合单机构信号（API 与评分消费）。"""
    with db_scope() as db:
        org = db.get(Organization, oid)
        name = org.canonical_name if org else ""
        rv = [dict(source=e.source, sentiment=e.sentiment, hit_keywords=e.hit_keywords,
                   match_confidence=e.match_confidence,
                   published_at=str(e.published_at) if e.published_at else None)
              for e in db.execute(select(ReviewEvidence).where(
                  ReviewEvidence.organization_id == oid)).scalars()]
        lg = [dict(record_type=r.record_type, match_confidence=r.match_confidence,
                   filing_date=str(r.filing_date) if r.filing_date else None)
              for r in db.execute(select(LegalRecord).where(
                  LegalRecord.organization_id == oid)).scalars()]
        return {"review": aggregate_review(rv, name), "legal": aggregate_legal(lg)}
