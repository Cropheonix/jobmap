"""筛选流水线编排：S0–S5 串行，输出 filter_trace 与三区处置。"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from backend.filters import confidence as conf_mod
from backend.filters import entity as entity_mod
from backend.filters import freshness as fresh_mod
from backend.filters import intent as intent_mod
from backend.filters import relevance as rel_mod
from backend.filters import validator as valid_mod

FILTER_VERSION = "1.0.0"

# 三档阈值（宽松/标准/严格），对应设计文档 §11；城市权重上限为共同下限保护
THRESHOLDS = {
    "loose":    {"rel_accept": 0.45, "rel_quarantine": 0.20, "c_review": 0.55, "saturation": 0.03},
    "standard": {"rel_accept": 0.55, "rel_quarantine": 0.30, "c_review": 0.70, "saturation": 0.05},
    "strict":   {"rel_accept": 0.65, "rel_quarantine": 0.40, "c_review": 0.80, "saturation": 0.08},
}

ZONE_ACCEPTED = "accepted"
ZONE_SUSPECT = "accepted_suspect"
ZONE_QUARANTINED = "quarantined"


@dataclass
class FilterResult:
    zone: str
    status: str
    confidence: float
    relevance: float
    entity_conf: float
    consistency: float
    flags: list[str] = field(default_factory=list)
    stages: list[dict] = field(default_factory=list)
    rejection_reason: str | None = None

    def to_trace(self) -> dict:
        return {
            "filter_version": FILTER_VERSION,
            "zone": self.zone,
            "status": self.status,
            "confidence": self.confidence,
            "relevance": self.relevance,
            "entity_conf": self.entity_conf,
            "consistency": self.consistency,
            "flags": self.flags,
            "stages": self.stages,
            "rejection_reason": self.rejection_reason,
        }


def run_filters(record: dict, intent: intent_mod.Intent, *, thresholds: dict | None = None,
                level: str = "standard", today: date | None = None,
                same_name_orgs_count: int = 0,
                existing_job_records: list[dict] | None = None) -> FilterResult:
    """对一条规范化记录执行 S0–S5。record 会被就地修改（字段级作废）。"""
    th = thresholds or THRESHOLDS[level if level in THRESHOLDS else "standard"]
    stages: list[dict] = []

    def stage(name: str, passed: bool, **kw):
        stages.append({"stage": name, "pass": passed, **kw})

    # S1 记录级必填（S0 意图由调用方完成并传入）
    required_err = valid_mod.check_required(record)
    if required_err:
        stage("S1", False, reason=required_err)
        return FilterResult(ZONE_QUARANTINED, "unverified", 0.0, 0.0, 0.0, 0.5,
                            flags=["missing_required"], stages=stages, rejection_reason="missing_required")
    # 问答/科普页（「XX是什么职位」）：非职位发布页
    if record.get("is_qa_page"):
        stage("S2", False, reason="qa_page")
        return FilterResult(ZONE_QUARANTINED, "unverified", 0.0, 0.0, 0.0, 0.5,
                            flags=["qa_page"], stages=stages, rejection_reason="qa_page")
    # S1 字段级
    field_flags = valid_mod.validate_fields(record, today)
    stage("S1", True, flags=field_flags)

    # S0 预过滤：用户排除词
    blob = " ".join([record.get("title") or "", record.get("description_summary") or ""]).lower()
    if any(term.lower() in blob for term in intent.exclude_terms):
        stage("S0", False, reason="user_excluded")
        return FilterResult(ZONE_QUARANTINED, "unverified", 0.0, 0.0, 0.0, 0.5,
                            flags=["user_excluded"], stages=stages, rejection_reason="user_excluded")

    # S2 相关性三档
    rel, rel_detail = rel_mod.relevance(intent, record)
    if rel < th["rel_quarantine"]:
        stage("S2", False, score=rel, detail=rel_detail, reason="low_relevance")
        return FilterResult(ZONE_QUARANTINED, "unverified", 0.0, rel, 0.0, 0.5,
                            flags=[f"rel={rel}"], stages=stages, rejection_reason="low_relevance")
    zone = ZONE_ACCEPTED if rel >= th["rel_accept"] else ZONE_SUSPECT
    # 城市硬规则：用户限定了城市而记录明确属于另一城市 → 至少降为疑似（远程/误标交排序与用户筛选）
    record_city = (record.get("city") or "").strip()
    if intent.city and record_city and record_city != intent.city:
        if zone == ZONE_ACCEPTED:
            zone = ZONE_SUSPECT
        field_flags.append("city_mismatch")
    stage("S2", True, score=rel, detail=rel_detail, zone=zone)

    # S3 实体消歧
    org = record.get("organization") or {}
    e_conf, ambiguous = entity_mod.entity_confidence(
        record_name=org.get("name") or "", record_uscc=org.get("uscc"),
        record_region=record.get("city") or record.get("district") or "",
        org_name=record.get("_matched_org_name"), org_uscc=record.get("_matched_org_uscc"),
        org_region=record.get("_matched_org_region"),
    )
    if entity_mod.is_ambiguous(org.get("name") or "", same_name_orgs_count):
        ambiguous = True
    if ambiguous:
        e_conf = min(e_conf, 0.6)
        field_flags.append("entity_ambiguous")
    if e_conf < 0.75:
        field_flags.append("entity_unconfirmed")
    stage("S3", True, entity_conf=e_conf, ambiguous=ambiguous)

    # S4 状态门控
    status, status_reason, fresh_flags = fresh_mod.status_gate(record, today)
    field_flags.extend(fresh_flags)
    stage("S4", True, status=status, reason=status_reason)

    # S5 一致性与置信度
    cons_score, conflict_flags = conf_mod.consistency(existing_job_records or [], record)
    field_flags.extend(conflict_flags)
    sources = record.get("sources") or [{}]
    c = conf_mod.compute_c(
        source_type=sources[0].get("source_type") or "user_import",
        extraction_tier=record.get("extraction_tier") or "text",
        field_completeness=conf_mod.field_completeness(record),
        entity_conf=e_conf, consistency_score=cons_score,
    )
    if c < th["c_review"] and zone != ZONE_QUARANTINED:
        field_flags.append("needs_review")
    stage("S5", True, confidence=c, consistency=cons_score)

    if status in ("expired", "closed"):
        field_flags.append("archived")
    return FilterResult(zone, status, c, rel, e_conf, cons_score,
                        flags=sorted(set(field_flags)), stages=stages, rejection_reason=None)
