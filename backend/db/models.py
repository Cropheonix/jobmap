"""ORM 模型（对应方案第 6 节数据库设计）。"""
from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.db.database import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _today() -> datetime:
    return datetime.now(timezone.utc).date()


class Organization(Base):
    __tablename__ = "organizations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    canonical_name: Mapped[str] = mapped_column(String(200), unique=True, index=True)
    aliases: Mapped[list] = mapped_column(JSON, default=list)
    type: Mapped[str] = mapped_column(String(20), default="unknown")  # studio/company/enterprise/institute/unknown
    industry: Mapped[str] = mapped_column(String(100), default="")
    official_site: Mapped[str] = mapped_column(String(500), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    size_band: Mapped[str] = mapped_column(String(50), default="")
    verified_projects: Mapped[list] = mapped_column(JSON, default=list)  # 可核实的代表项目
    uscc: Mapped[str] = mapped_column(String(30), default="", index=True)             # 统一社会信用代码
    registered_region: Mapped[str] = mapped_column(String(50), default="")            # 注册地区

    jobs: Mapped[list["Job"]] = relationship(back_populates="organization")


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"), index=True)
    title: Mapped[str] = mapped_column(String(300), index=True)
    description_summary: Mapped[str] = mapped_column(Text, default="")
    city: Mapped[str] = mapped_column(String(50), index=True, default="")
    district: Mapped[str] = mapped_column(String(50), default="")
    address: Mapped[str] = mapped_column(String(300), default="")
    salary_min: Mapped[float | None] = mapped_column(Float, nullable=True)  # 单位：千元/月
    salary_max: Mapped[float | None] = mapped_column(Float, nullable=True)
    salary_months: Mapped[int | None] = mapped_column(Integer, nullable=True)
    experience: Mapped[str] = mapped_column(String(50), default="不限")
    education: Mapped[str] = mapped_column(String(50), default="")
    job_category: Mapped[str] = mapped_column(String(50), index=True, default="其他行业")
    industry: Mapped[str] = mapped_column(String(100), default="")
    keywords: Mapped[list] = mapped_column(JSON, default=list)
    published_at: Mapped[datetime | None] = mapped_column(Date, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="unverified", index=True)
    status_reason: Mapped[str] = mapped_column(String(300), default="")
    career_notes: Mapped[str] = mapped_column(String(300), default="")  # 公开可见的成长路径描述
    first_seen: Mapped[datetime] = mapped_column(Date, default=_today)
    last_checked: Mapped[datetime | None] = mapped_column(Date, nullable=True)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)
    # M2 筛选算法字段（docs/filter-algorithm-design.md §9）
    filter_zone: Mapped[str] = mapped_column(String(20), default="accepted", index=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    rejection_reason: Mapped[str] = mapped_column(String(50), default="")
    filter_trace: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    discovered_via: Mapped[str] = mapped_column(String(30), default="")

    organization: Mapped[Organization] = relationship(back_populates="jobs")
    sources: Mapped[list["JobSource"]] = relationship(
        back_populates="job", cascade="all, delete-orphan"
    )
    locations: Mapped[list["Location"]] = relationship(
        back_populates="job", cascade="all, delete-orphan"
    )
    score: Mapped["Score | None"] = relationship(
        back_populates="job", cascade="all, delete-orphan", uselist=False
    )
    action: Mapped["JobAction | None"] = relationship(
        back_populates="job", cascade="all, delete-orphan", uselist=False
    )


class JobSource(Base):
    __tablename__ = "job_sources"
    __table_args__ = (
        # 仅当 source_post_id 非空时唯一（空串表示来源未提供帖子 ID）
        Index("uq_provider_post", "provider", "source_post_id", unique=True,
              sqlite_where=text("source_post_id IS NOT NULL AND source_post_id != ''")),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id"), index=True)
    provider: Mapped[str] = mapped_column(String(100))
    original_url: Mapped[str] = mapped_column(String(800))
    source_type: Mapped[str] = mapped_column(String(30), index=True)
    source_post_id: Mapped[str] = mapped_column(String(200), default="")
    access_status: Mapped[str] = mapped_column(String(30), default="accessible")
    observed_at: Mapped[datetime | None] = mapped_column(Date, nullable=True)
    evidence_excerpt: Mapped[str] = mapped_column(String(500), default="")
    license_note: Mapped[str] = mapped_column(String(300), default="")

    job: Mapped[Job] = relationship(back_populates="sources")


class Location(Base):
    __tablename__ = "locations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id"), index=True)
    raw_lon: Mapped[float | None] = mapped_column(Float, nullable=True)
    raw_lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    raw_crs: Mapped[str] = mapped_column(String(20), default="wgs84")
    wgs84_lon: Mapped[float | None] = mapped_column(Float, nullable=True)
    wgs84_lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    precision: Mapped[str] = mapped_column(String(20), default="unlocatable")  # building/block/district/unlocatable
    address_basis: Mapped[str] = mapped_column(String(200), default="")
    geocode_source: Mapped[str] = mapped_column(String(100), default="")
    confidence: Mapped[float] = mapped_column(Float, default=0.0)

    job: Mapped[Job] = relationship(back_populates="locations")


class SearchTask(Base):
    __tablename__ = "search_tasks"

    id: Mapped[str] = mapped_column(Integer, primary_key=True)
    query_json: Mapped[dict] = mapped_column(JSON, default=dict)
    state: Mapped[str] = mapped_column(String(20), default="pending")  # pending/running/done/cancelled/failed
    started_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    providers: Mapped[list] = mapped_column(JSON, default=list)
    counts: Mapped[dict] = mapped_column(JSON, default=dict)
    errors: Mapped[list] = mapped_column(JSON, default=list)
    steps: Mapped[list] = mapped_column(JSON, default=list)
    funnel: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class Score(Base):
    __tablename__ = "scores"
    __table_args__ = (UniqueConstraint("job_id", "algorithm_version", name="uq_job_algo"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id"), index=True)
    algorithm_version: Mapped[str] = mapped_column(String(20), default="1.0.0")
    weights_json: Mapped[dict] = mapped_column(JSON, default=dict)
    subscores_json: Mapped[dict] = mapped_column(JSON, default=dict)
    components_json: Mapped[dict] = mapped_column(JSON, default=dict)
    missing_fields: Mapped[list] = mapped_column(JSON, default=list)
    total_score: Mapped[float] = mapped_column(Float, default=0.0)
    completeness: Mapped[float] = mapped_column(Float, default=0.0)
    computed_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

    job: Mapped[Job] = relationship(back_populates="score")


class UserPreference(Base):
    __tablename__ = "user_preferences"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)  # 单例行 id=1
    keywords: Mapped[list] = mapped_column(JSON, default=list)
    city: Mapped[str] = mapped_column(String(50), default="")
    salary_expect_min: Mapped[float | None] = mapped_column(Float, nullable=True)
    salary_expect_max: Mapped[float | None] = mapped_column(Float, nullable=True)
    commute_origin_lon: Mapped[float | None] = mapped_column(Float, nullable=True)
    commute_origin_lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    org_type_pref: Mapped[str] = mapped_column(String(20), default="")
    weights: Mapped[dict] = mapped_column(JSON, default=dict)
    ranking_mode: Mapped[str] = mapped_column(String(20), default="score")
    enabled_providers: Mapped[list] = mapped_column(JSON, default=list)
    keep_undisclosed_salary: Mapped[bool] = mapped_column(Boolean, default=True)
    tile_url: Mapped[str] = mapped_column(String(500), default="")
    tile_attribution: Mapped[str] = mapped_column(String(300), default="")
    tile_crs: Mapped[str] = mapped_column(String(10), default="wgs84")
    external_searxng_url: Mapped[str] = mapped_column(String(300), default="")
    filter_level: Mapped[str] = mapped_column(String(10), default="standard")
    browser_fetch_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)


class JobAction(Base):
    __tablename__ = "job_actions"

    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id"), primary_key=True)
    status: Mapped[str] = mapped_column(String(20), default="none")
    note: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)

    job: Mapped[Job] = relationship(back_populates="action")


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_type: Mapped[str] = mapped_column(String(50), index=True)
    record_id: Mapped[str] = mapped_column(String(50), default="")
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=_now)
    detail: Mapped[dict] = mapped_column(JSON, default=dict)


class ReviewEvidence(Base):
    """公司口碑证据（帖子级元数据，不存发帖人身份字段）。"""
    __tablename__ = "review_evidence"
    __table_args__ = (UniqueConstraint("organization_id", "url", name="uq_org_review_url"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"), index=True)
    source: Mapped[str] = mapped_column(String(20))          # zhihu/xhs/tieba/other
    url: Mapped[str] = mapped_column(String(800))
    title: Mapped[str] = mapped_column(String(500), default="")
    snippet: Mapped[str] = mapped_column(String(1000), default="")
    published_at: Mapped[str | None] = mapped_column(Date, nullable=True)
    sentiment: Mapped[str] = mapped_column(String(10), default="neutral")  # negative/neutral/positive
    hit_keywords: Mapped[list] = mapped_column(JSON, default=list)
    match_confidence: Mapped[float] = mapped_column(Float, default=0.0)
    observed_at: Mapped[datetime] = mapped_column(Date, default=_today)


class LegalRecord(Base):
    """公司法律与信用记录（案件/公告级元数据）。"""
    __tablename__ = "legal_records"
    __table_args__ = (UniqueConstraint("organization_id", "url", name="uq_org_legal_url"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"), index=True)
    source: Mapped[str] = mapped_column(String(30))          # creditchina/zxgk/hr_bulletin/user_import/discovery
    record_type: Mapped[str] = mapped_column(String(30))     # labor_arbitration/contract_dispute/enforcement/admin_penalty/wage_blacklist
    case_no: Mapped[str] = mapped_column(String(100), default="")
    title: Mapped[str] = mapped_column(String(500), default="")
    summary: Mapped[str] = mapped_column(String(1000), default="")
    filing_date: Mapped[datetime | None] = mapped_column(Date, nullable=True)
    match_confidence: Mapped[float] = mapped_column(Float, default=0.0)
    url: Mapped[str] = mapped_column(String(800))
    observed_at: Mapped[datetime] = mapped_column(Date, default=_today)
