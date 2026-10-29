"""记录装载器：把结构化职位记录写入数据库（含去重合并），供种子数据、示例搜索源、导入共用。"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.db.models import Job, JobAction, JobSource, Location, Organization
from backend.pipeline import dedup


def _parse_date(value):
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value.date()
    try:
        return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def get_or_create_organization(db: Session, org: dict) -> Organization:
    name = (org.get("name") or "").strip()
    existing = db.execute(
        select(Organization).where(Organization.canonical_name == name)
    ).scalar_one_or_none()
    if existing:
        return existing
    row = Organization(
        canonical_name=name,
        aliases=org.get("aliases") or [],
        type=org.get("type") or "unknown",
        industry=org.get("industry") or "",
        official_site=org.get("official_site") or "",
        description=org.get("description") or "",
        size_band=org.get("size_band") or "",
        verified_projects=org.get("verified_projects") or [],
    )
    db.add(row)
    db.flush()
    return row


def upsert_job_record(db: Session, record: dict) -> tuple[str, Job | None, str]:
    """写入一条职位记录。返回 (结果, 岗位, 说明)，结果 ∈ created/merged/rejected。"""
    org_data = record.get("organization") or {}
    title = (record.get("title") or "").strip()
    if not title or not org_data.get("name"):
        return "rejected", None, "缺少职位名或公司名"
    sources = record.get("sources") or []
    if not sources:
        return "rejected", None, "缺少来源信息（至少一个来源 URL）"

    org = get_or_create_organization(db, org_data)
    city = record.get("city") or ""
    district = record.get("district") or ""

    candidates = db.execute(
        select(Job).where(Job.organization_id == org.id, Job.city == city)
    ).scalars().all()

    canonical_urls = {dedup.normalize_url(s.get("url") or "") for s in sources} - {""}
    post_ids = {(s.get("provider") or "", str(s.get("source_post_id") or "")) for s in sources
                if s.get("provider") and s.get("source_post_id")}
    verdict = dedup.classify(
        canonical_urls=canonical_urls,
        provider_post_ids=post_ids,
        company_norm=dedup.normalize_company(org.canonical_name),
        title=title,
        city=city,
        district=district,
        existing=candidates,
    )

    if verdict.kind in ("strong", "suspected"):
        job = db.get(Job, verdict.job_id)
        _merge_into(db, job, record, sources, verdict.reason)
        return "merged", job, verdict.reason

    job = Job(
        organization_id=org.id,
        title=title,
        description_summary=record.get("description_summary") or "",
        city=city,
        district=district,
        address=record.get("address") or "",
        salary_min=record.get("salary_min"),
        salary_max=record.get("salary_max"),
        salary_months=record.get("salary_months"),
        experience=record.get("experience") or "不限",
        education=record.get("education") or "",
        job_category=record.get("job_category") or "其他行业",
        industry=record.get("industry") or org.industry,
        keywords=record.get("keywords") or [],
        published_at=_parse_date(record.get("published_at")),
        status=record.get("status") or "unverified",
        status_reason=record.get("status_reason") or "",
        career_notes=record.get("career_notes") or "",
        last_checked=_parse_date(record.get("last_checked")),
        is_demo=bool(record.get("is_demo")),
    )
    db.add(job)
    db.flush()
    _attach_sources(db, job, sources)
    _attach_location(db, job, record.get("location") or {})
    db.add(JobAction(job_id=job.id, status="none", note=""))
    return "created", job, ""


def _attach_sources(db: Session, job: Job, sources: list[dict]) -> None:
    for s in sources:
        db.add(JobSource(
            job_id=job.id,
            provider=s.get("provider") or "未命名来源",
            original_url=s.get("url") or "",
            source_type=s.get("source_type") or "user_import",
            source_post_id=str(s.get("source_post_id") or ""),
            access_status=s.get("access_status") or "accessible",
            observed_at=_parse_date(s.get("observed_at")),
            evidence_excerpt=s.get("evidence_excerpt") or "",
            license_note=s.get("license_note") or "",
        ))


def _attach_location(db: Session, job: Job, loc: dict) -> None:
    lon, lat = loc.get("lon"), loc.get("lat")
    if lon is None or lat is None:
        precision = loc.get("precision") or "unlocatable"
        db.add(Location(job_id=job.id, precision="unlocatable" if precision == "unlocatable" else precision,
                        address_basis=loc.get("address_basis") or "", geocode_source=loc.get("geocode_source") or "import"))
        return
    db.add(Location(
        job_id=job.id,
        raw_lon=lon, raw_lat=lat, raw_crs=loc.get("crs") or "wgs84",
        wgs84_lon=lon, wgs84_lat=lat,  # 导入数据需自行保证已转 WGS-84，或声明 crs
        precision=loc.get("precision") or "district",
        address_basis=loc.get("address_basis") or "",
        geocode_source=loc.get("geocode_source") or "import",
        confidence=float(loc.get("confidence") or 0.5),
    ))


def _merge_into(db: Session, job: Job, record: dict, sources: list[dict], reason: str) -> None:
    """合并重复记录：保留原岗位主体，追加新来源；缺失字段择优补齐。"""
    existing_urls = {dedup.normalize_url(s.original_url) for s in job.sources}
    for s in sources:
        if dedup.normalize_url(s.get("url") or "") in existing_urls:
            continue
        existing_urls.add(dedup.normalize_url(s.get("url") or ""))
        db.add(JobSource(
            job_id=job.id,
            provider=s.get("provider") or "未命名来源",
            original_url=s.get("url") or "",
            source_type=s.get("source_type") or "user_import",
            source_post_id=str(s.get("source_post_id") or ""),
            access_status=s.get("access_status") or "accessible",
            observed_at=_parse_date(s.get("observed_at")),
            evidence_excerpt=s.get("evidence_excerpt") or "",
            license_note=s.get("license_note") or "",
        ))
    # 缺失字段补齐（不覆盖已有值）
    if not job.salary_max and record.get("salary_max"):
        job.salary_min, job.salary_max = record.get("salary_min"), record.get("salary_max")
        job.salary_months = record.get("salary_months") or job.salary_months
    if not job.address and record.get("address"):
        job.address = record["address"]
    if not job.published_at:
        job.published_at = _parse_date(record.get("published_at"))
    new_check = _parse_date(record.get("last_checked"))
    if new_check and (not job.last_checked or new_check > job.last_checked):
        job.last_checked = new_check
    if record.get("status") == "active" and job.status == "unverified":
        job.status = "active"
        job.status_reason = f"合并来源后状态更新（{reason}）"
