"""S2 语义相关性：rel = 0.45·标题 + 0.20·正文 + 0.20·城市 + 0.15·类别，三档分流。"""
from __future__ import annotations

from backend.filters.intent import Intent

WEIGHTS = {"title": 0.45, "body": 0.20, "city": 0.20, "category": 0.15}


def _title_hit(intent: Intent, title: str) -> float:
    if not title:
        return 0.0
    t = title.lower()
    primary = intent.primary_role.lower()
    if primary and primary in t:
        return 1.0
    terms = [x for x in intent.role_terms if x]
    if not terms:
        return 0.5  # 无岗位词（纯城市查询）：标题维度取中性
    hits = sum(1 for x in terms if x in t)
    if hits == len(terms):
        return 0.8
    return round(0.6 * hits / len(terms), 3)


def _body_hit(intent: Intent, *texts: str) -> float:
    blob = " ".join(x or "" for x in texts).lower()
    terms = [x for x in intent.role_terms if x]
    if not terms:
        return 0.5
    return min(1.0, sum(1 for x in terms if x in blob) / len(terms))


def _city_match(intent: Intent, city: str) -> float:
    if not intent.city:
        return 1.0  # 用户未限定城市：不惩罚
    if not (city or "").strip():
        return 0.5  # 记录无城市字段：中性
    return 1.0 if city.strip() == intent.city else 0.0


def _category_match(intent: Intent, record: dict) -> float:
    if not intent.category:
        return 1.0
    cats = {record.get("job_category") or "", record.get("industry") or ""}
    if intent.category in cats:
        return 1.0
    return 0.5 if not any(cats) else 0.0


def relevance(intent: Intent, record: dict) -> tuple[float, dict]:
    title = record.get("title") or ""
    body = " ".join([
        record.get("description_summary") or "",
        " ".join(record.get("keywords") or []),
        (record.get("organization") or {}).get("name") or "",
    ])
    detail = {
        "title_hit": _title_hit(intent, title),
        "body_hit": _body_hit(intent, record.get("description_summary") or "", " ".join(record.get("keywords") or [])),
        "city_match": _city_match(intent, record.get("city") or ""),
        "category_match": _category_match(intent, record),
    }
    score = (WEIGHTS["title"] * detail["title_hit"] + WEIGHTS["body"] * detail["body_hit"]
             + WEIGHTS["city"] * detail["city_match"] + WEIGHTS["category"] * detail["category_match"])
    return round(min(1.0, max(0.0, score)), 3), detail
