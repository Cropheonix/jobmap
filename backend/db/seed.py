"""首次启动时把示例数据灌入数据库并完成首轮评分。"""
from sqlalchemy import func, select

from backend.core.config import ALGORITHM_VERSION, DEFAULT_WEIGHTS
from backend.db.database import db_scope
from backend.db.models import AuditEvent, Job, Score, UserPreference
from backend.pipeline.loader import upsert_job_record
from backend.providers.sample_provider import SampleProvider
from backend.ranking.engine import score_job, weighted_total


def _load_signals(db) -> dict[int, dict]:
    """按机构聚合口碑/法律证据 → 信号（评分 V 维度消费）。"""
    from sqlalchemy import select as _select

    from backend.db.models import LegalRecord, Organization, ReviewEvidence
    from backend.signals.service import aggregate_legal, aggregate_review

    out: dict[int, dict] = {}
    orgs = {o.id: o for o in db.execute(_select(Organization)).scalars()}
    reviews = db.execute(_select(ReviewEvidence)).scalars().all()
    legals = db.execute(_select(LegalRecord)).scalars().all()
    for org_id, org in orgs.items():
        rv = [dict(source=e.source, title=e.title, snippet=e.snippet, sentiment=e.sentiment,
                   hit_keywords=e.hit_keywords, match_confidence=e.match_confidence,
                   published_at=str(e.published_at) if e.published_at else None)
              for e in reviews if e.organization_id == org_id]
        lg = [dict(record_type=r.record_type, filing_date=str(r.filing_date) if r.filing_date else None,
                   match_confidence=r.match_confidence)
              for r in legals if r.organization_id == org_id]
        if rv or lg:
            out[org_id] = {"review": aggregate_review(rv, orgs[org_id].canonical_name),
                           "legal": aggregate_legal(lg)}
    return out


def ensure_seeded() -> None:
    with db_scope() as db:
        prefs = db.get(UserPreference, 1) or UserPreference(id=1)
        if not db.get(UserPreference, 1):
            db.add(prefs)
        prefs.weights = DEFAULT_WEIGHTS
        prefs.enabled_providers = ["sample", "user_import"]

        job_count = db.execute(select(func.count(Job.id))).scalar() or 0
        if job_count == 0:
            provider = SampleProvider()
            for record in provider._load_records():
                try:
                    upsert_job_record(db, record)
                except Exception as exc:
                    db.add(AuditEvent(event_type="seed_error", detail={"error": str(exc),
                                                                       "title": record.get("title")}))
            db.add(AuditEvent(event_type="seed", detail={"source": "sample-data", }))
        db.commit()
        recompute_all_scores()


def recompute_all_scores() -> dict:
    """按当前偏好重算全部岗位的子分（总分由权重即时归一化，展示时计算）。"""
    import time
    t0 = time.perf_counter()
    with db_scope() as db:
        prefs = db.get(UserPreference, 1) or UserPreference(id=1, weights=DEFAULT_WEIGHTS)
        if not db.get(UserPreference, 1):
            db.add(prefs)
        weights = {**DEFAULT_WEIGHTS, **(prefs.weights or {})}
        signals_by_org = _load_signals(db)
        n = 0
        for job in db.execute(select(Job)).scalars():
            result = score_job(job, prefs, signals=signals_by_org.get(job.organization_id))
            score = db.execute(
                select(Score).where(Score.job_id == job.id,
                                    Score.algorithm_version == ALGORITHM_VERSION)
            ).scalar_one_or_none()
            if not score:
                score = Score(job_id=job.id, algorithm_version=ALGORITHM_VERSION)
                db.add(score)
            score.weights_json = weights
            score.subscores_json = result["subscores"]
            score.components_json = result["components"]
            score.missing_fields = result["missing_fields"]
            score.completeness = result["completeness"]
            score.total_score = weighted_total(result["subscores"], weights)
            n += 1
        db.add(AuditEvent(event_type="recompute", detail={"jobs": n}))
    return {"jobs": n, "elapsed_ms": round((time.perf_counter() - t0) * 1000, 1)}
