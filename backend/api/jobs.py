"""职位接口：列表/详情/手动登记/求职进度。"""
from datetime import date, timedelta

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from backend.api.dto import job_dto
from backend.api.system import get_prefs
from backend.core.config import ACTION_STATUSES
from backend.core.security import require_token, validate_public_http_url
from backend.db.database import get_db
from backend.db.models import (
    AuditEvent,
    Job,
    JobAction,
    JobSource,
    Organization,
    SearchTask,
)
from backend.pipeline.loader import upsert_job_record
from backend.ranking.engine import status_group

router = APIRouter()


def _multi(value: str | None) -> list[str]:
    return [v for v in (value or "").split(",") if v]


@router.get("/jobs")
def list_jobs(
    q: str | None = None,
    city: str | None = None,
    district: str | None = None,
    category: str | None = None,
    status: str | None = None,
    source_type: str | None = None,
    precision: str | None = None,
    experience: str | None = None,
    action_status: str | None = None,
    salary_min: float | None = None,
    salary_undisclosed: str = "keep",
    published_within_days: int | None = None,
    order: str = "score",
    zone: str = "active",   # active=榜单区(accepted+suspect) / quarantine=隔离区 / all=全部
    offset: int = 0,
    limit: int = Query(default=500, le=2000),
    db: Session = Depends(get_db),
):
    prefs = get_prefs(db)
    stmt = select(Job)
    if zone == "quarantine":
        stmt = stmt.where(Job.filter_zone == "quarantined")
    elif zone != "all":
        stmt = stmt.where((Job.filter_zone.is_(None)) | (Job.filter_zone != "quarantined"))
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.join(Organization, Job.organization_id == Organization.id).where(
            or_(Job.title.like(like), Job.description_summary.like(like),
                Organization.canonical_name.like(like))
        )
    if city:
        stmt = stmt.where(Job.city == city)
    if district:
        stmt = stmt.where(Job.district == district)
    if category:
        stmt = stmt.where(Job.job_category.in_(_multi(category)))
    if status:
        stmt = stmt.where(Job.status.in_(_multi(status)))
    if experience:
        stmt = stmt.where(Job.experience == experience)
    if source_type:
        stmt = stmt.where(Job.sources.any(JobSource.source_type.in_(_multi(source_type))))
    if precision:
        stmt = stmt.where(Job.locations.any())
    if salary_min is not None:
        stmt = stmt.where((Job.salary_max or 0) >= salary_min)
    if salary_undisclosed == "exclude":
        stmt = stmt.where(Job.salary_max.isnot(None))
    if published_within_days:
        cutoff = date.today() - timedelta(days=published_within_days)
        stmt = stmt.where(Job.published_at >= cutoff)
    if action_status:
        stmt = stmt.where(Job.action.has(JobAction.status.in_(_multi(action_status))))

    jobs = list(db.execute(stmt).scalars().unique())
    items = [job_dto(db, j, prefs.weights) for j in jobs]

    def sort_key(dto):
        total = dto["total_score"] if dto["total_score"] is not None else -1
        if order == "newest":
            return (dto["published_at"] is None, dto["published_at"] or "", total)
        if order == "salary":
            return (dto["salary_max"] is None, dto["salary_max"] or 0, total)
        if order == "team":
            order_map = {"studio": 0, "company": 1, "enterprise": 2, "institute": 3, "unknown": 4}
            return (status_group(dto["status"]), order_map.get(dto["organization"]["type"], 9),
                    -total, dto["published_at"] or "")
        return (status_group(dto["status"]), -total,
                -(dto["subscores"].get("R") or 0), dto["published_at"] or "", dto["id"])

    items.sort(key=sort_key)
    total = len(items)
    return {
        "total": total,
        "items": items[offset: offset + limit],
        "weights": prefs.weights,
        "ranking_mode": prefs.ranking_mode,
    }


@router.get("/jobs/{job_id}")
def job_detail(job_id: int, db: Session = Depends(get_db)):
    job = db.get(Job, job_id)
    if not job:
        raise HTTPException(404, "职位不存在")
    prefs = get_prefs(db)
    return job_dto(db, job, prefs.weights)


@router.patch("/jobs/{job_id}/action")
def update_action(
    job_id: int,
    body: dict,
    db: Session = Depends(get_db),
    token: str | None = Header(default=None, alias="X-JobMap-Token"),
):
    if not require_token(token):
        raise HTTPException(403, "缺少或错误的本地会话令牌")
    job = db.get(Job, job_id)
    if not job:
        raise HTTPException(404, "职位不存在")
    status = body.get("status")
    if status and status not in ACTION_STATUSES:
        raise HTTPException(422, f"非法状态：{status}")
    action = job.action or JobAction(job_id=job.id)
    if status:
        action.status = status
    if "note" in body:
        action.note = (body.get("note") or "")[:2000]
    db.add(action)
    db.add(AuditEvent(event_type="action_update", record_id=str(job.id),
                      detail={"status": action.status}))
    db.commit()
    prefs = get_prefs(db)
    return job_dto(db, job, prefs.weights)


@router.post("/jobs/manual")
def manual_register(
    body: dict,
    db: Session = Depends(get_db),
    token: str | None = Header(default=None, alias="X-JobMap-Token"),
):
    """手动登记一条职位 URL（v1 仅登记元数据并标记待核查，不抓取正文；抓取在 M2 提供）。"""
    if not require_token(token):
        raise HTTPException(403, "缺少或错误的本地会话令牌")
    url = (body.get("url") or "").strip()
    ok, reason = validate_public_http_url(url)
    if not ok:
        raise HTTPException(422, f"URL 校验失败：{reason}")
    record = {
        "organization": {"name": (body.get("company") or "手动登记").strip(), "type": "unknown"},
        "title": (body.get("title") or "手动登记（待补充信息）").strip(),
        "description_summary": body.get("note") or "",
        "city": body.get("city") or "",
        "district": body.get("district") or "",
        "status": "unverified",
        "status_reason": "手动登记，等待核实",
        "sources": [{
            "provider": body.get("provider") or "手动登记",
            "url": url,
            "source_type": "user_import",
            "access_status": "pending",
            "observed_at": str(date.today()),
            "evidence_excerpt": "用户手动登记，尚未访问页面",
            "license_note": "待 M2 接入抓取后补充证据",
        }],
        "is_demo": False,
    }
    result, job, why = upsert_job_record(db, record)
    db.add(AuditEvent(event_type="manual_register", record_id=str(job.id if job else ""),
                      detail={"result": result, "url": url}))
    db.commit()
    prefs = get_prefs(db)
    return {"result": result, "reason": why, "job": job_dto(db, job, prefs.weights) if job else None}


@router.get("/stats")
def stats(db: Session = Depends(get_db)):
    prefs = get_prefs(db)

    def count(stmt):
        return db.execute(select(func.count()).select_from(stmt.subquery())).scalar() or 0

    jobs = db.execute(select(Job)).scalars().unique().all()
    active = sum(1 for j in jobs if j.status == "active")
    unverified = sum(1 for j in jobs if j.status == "unverified")
    expired = sum(1 for j in jobs if j.status in ("expired", "closed"))
    geo = sum(1 for j in jobs if j.locations and j.locations[0].wgs84_lon is not None
              and j.locations[0].precision != "unlocatable")
    week_ago = date.today() - timedelta(days=7)
    new7 = sum(1 for j in jobs if j.published_at and j.published_at >= week_ago)
    sources = db.execute(select(func.count(JobSource.id))).scalar() or 0
    orgs = db.execute(select(func.count(Organization.id))).scalar() or 0
    last_task = db.execute(
        select(SearchTask).order_by(SearchTask.started_at.desc()).limit(1)
    ).scalars().first()
    demo = sum(1 for j in jobs if j.is_demo)
    quarantined = sum(1 for j in jobs if j.filter_zone == "quarantined")
    discovered = sum(1 for j in jobs if j.discovered_via == "searxng_discovery")
    return {
        "total_jobs": len(jobs),
        "valid_jobs": active + unverified,
        "active_jobs": active,
        "unverified_jobs": unverified,
        "expired_jobs": expired,
        "geo_located": geo,
        "sources_total": sources,
        "organizations": orgs,
        "new_last_7d": new7,
        "demo_jobs": demo,
        "quarantined_jobs": quarantined,
        "discovered_jobs": discovered,
        "last_scan": (last_task.ended_at or last_task.started_at).isoformat() if last_task else None,
        "weights": prefs.weights,
    }
