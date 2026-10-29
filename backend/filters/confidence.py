"""S5 多源一致性与综合置信度 C。"""
from __future__ import annotations

from backend.core.config import SOURCE_TYPE_TRUST

EXTRACTION_TIERS = {"jsonld": 1.0, "dom": 0.85, "text": 0.60, "snippet": 0.40}

C_WEIGHTS = {"source_trust": 0.30, "extraction_tier": 0.20,
             "field_completeness": 0.20, "entity_conf": 0.15, "consistency": 0.15}


def consistency(existing_records: list[dict], new_record: dict) -> tuple[float, list[str]]:
    """多源一致性：≥2 源关键字段一致 1.0；单源 0.5；字段冲突 0.2 + conflict 标记。"""
    conflict_flags: list[str] = []
    if not existing_records:
        return 0.5, conflict_flags
    for field in ("salary_min", "salary_max", "city", "district", "experience"):
        new_v = new_record.get(field)
        if new_v is None:
            continue
        olds = {r.get(field) for r in existing_records if r.get(field) is not None}
        if olds and new_v not in olds:
            # 允许薪资区间部分重叠的软冲突
            if field.startswith("salary") and _range_overlap(new_v, olds):
                continue
            conflict_flags.append(f"conflict:{field}")
    if conflict_flags:
        return 0.2, conflict_flags
    return 1.0, conflict_flags


def _range_overlap(value, olds) -> bool:
    try:
        v = float(value)
        return any(abs(float(o) - v) <= max(2.0, v * 0.2) for o in olds)
    except (TypeError, ValueError):
        return False


def compute_c(*, source_type: str, extraction_tier: str, field_completeness: float,
              entity_conf: float, consistency_score: float) -> float:
    values = {
        "source_trust": SOURCE_TYPE_TRUST.get(source_type, 0.40),
        "extraction_tier": EXTRACTION_TIERS.get(extraction_tier, 0.40),
        "field_completeness": max(0.0, min(1.0, field_completeness / 100.0)),
        "entity_conf": entity_conf,
        "consistency": consistency_score,
    }
    total = sum(C_WEIGHTS[k] * v for k, v in values.items())
    return round(total, 3)


def field_completeness(record: dict) -> float:
    """轻量完整度（0-100）：筛选阶段用，与 M1 评分引擎的加权完整度口径独立。"""
    checks = [record.get("title"), (record.get("organization") or {}).get("name"),
              record.get("city"), record.get("district"), record.get("salary_max"),
              record.get("published_at"), (record.get("sources") or [{}])[0].get("url"),
              record.get("location")]
    return round(100 * sum(1 for c in checks if c) / len(checks), 1)
