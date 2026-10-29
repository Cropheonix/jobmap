"""职位 DTO 序列化（API 返回结构）。"""
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.core.config import (
    ACTION_STATUSES,
    JOB_STATUS,
    LOCATION_PRECISION,
    ORG_TYPES,
    SOURCE_TYPES,
)
from backend.db.models import Job, Score
from backend.ranking.engine import weighted_total


def salary_display(salary_min, salary_max, salary_months) -> str:
    if not salary_max and not salary_min:
        return "未披露"
    if salary_min and salary_max:
        text = f"{salary_min:g}-{salary_max:g}K"
    else:
        text = f"{salary_max or salary_min:g}K"
    if salary_months and salary_months != 12:
        text += f"·{salary_months}薪"
    return text


def job_dto(db: Session, job: Job, weights: dict | None = None) -> dict:
    org = job.organization
    loc = job.locations[0] if job.locations else None
    sources = sorted(job.sources, key=lambda s: (s.observed_at is None, s.observed_at), reverse=True)
    score = db.execute(
        select(Score).where(Score.job_id == job.id).order_by(Score.computed_at.desc())
    ).scalars().first()
    action = job.action
    subscores = (score.subscores_json if score else None) or {}
    total = weighted_total(subscores, weights) if subscores else None

    return {
        "id": job.id,
        "title": job.title,
        "description_summary": job.description_summary,
        "city": job.city,
        "district": job.district,
        "address": job.address,
        "organization": {
            "id": org.id if org else None,
            "name": org.canonical_name if org else "",
            "type": org.type if org else "unknown",
            "type_label": ORG_TYPES.get(org.type if org else "", "未知类型"),
            "industry": org.industry if org else "",
            "size_band": org.size_band if org else "",
            "verified_projects": org.verified_projects if org else [],
        },
        "salary_min": job.salary_min,
        "salary_max": job.salary_max,
        "salary_months": job.salary_months,
        "salary_display": salary_display(job.salary_min, job.salary_max, job.salary_months),
        "experience": job.experience,
        "education": job.education,
        "job_category": job.job_category,
        "industry": job.industry,
        "keywords": job.keywords or [],
        "published_at": str(job.published_at) if job.published_at else None,
        "last_checked": str(job.last_checked) if job.last_checked else None,
        "first_seen": str(job.first_seen) if job.first_seen else None,
        "status": job.status,
        "status_label": JOB_STATUS.get(job.status, job.status),
        "status_reason": job.status_reason,
        "is_demo": bool(job.is_demo),
        # M2 筛选算法字段
        "filter_zone": job.filter_zone or "accepted",
        "confidence": job.confidence,
        "rejection_reason": job.rejection_reason or "",
        "filter_flags": (job.filter_trace or {}).get("flags", []) if isinstance(job.filter_trace, dict) else [],
        "discovered_via": job.discovered_via or "",
        "sources": [
            {
                "provider": s.provider,
                "url": s.original_url,
                "source_type": s.source_type,
                "source_type_label": SOURCE_TYPES.get(s.source_type, s.source_type),
                "access_status": s.access_status,
                "observed_at": str(s.observed_at) if s.observed_at else None,
                "evidence_excerpt": s.evidence_excerpt,
                "license_note": s.license_note,
            }
            for s in sources
        ],
        "sources_count": len(sources),
        "source_labels": sorted({SOURCE_TYPES.get(s.source_type, s.source_type) for s in sources}),
        "location": (
            {
                "lon": loc.wgs84_lon,
                "lat": loc.wgs84_lat,
                "precision": loc.precision,
                "precision_label": LOCATION_PRECISION.get(loc.precision, loc.precision),
                "address_basis": loc.address_basis,
                "confidence": loc.confidence,
            }
            if loc and loc.wgs84_lon is not None and loc.wgs84_lat is not None
            else None
        ),
        "location_precision": loc.precision if loc else "unlocatable",
        "location_precision_label": LOCATION_PRECISION.get(loc.precision if loc else "", "无法定位"),
        "subscores": subscores,
        "components": (score.components_json if score else None) or {},
        "missing_fields": (score.missing_fields if score else None) or [],
        "completeness": (score.completeness if score is not None and score.completeness is not None else None),
        "total_score": total,
        "action": {
            "status": action.status if action else "none",
            "status_label": ACTION_STATUSES.get(action.status if action else "none", "未标记"),
            "note": action.note if action else "",
        },
    }
