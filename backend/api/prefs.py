"""偏好设置与评分重算。"""
from fastapi import APIRouter, Depends, Header, HTTPException

from backend.api.system import get_prefs
from backend.core.config import DEFAULT_WEIGHTS, RANKING_MODES
from backend.core.security import require_token
from backend.db.database import get_db
from backend.db.models import AuditEvent

router = APIRouter()


@router.get("/preferences")
def read_preferences(db=Depends(get_db)):
    prefs = get_prefs(db)
    return {
        "keywords": prefs.keywords or [],
        "city": prefs.city,
        "salary_expect_min": prefs.salary_expect_min,
        "salary_expect_max": prefs.salary_expect_max,
        "commute_origin_lon": prefs.commute_origin_lon,
        "commute_origin_lat": prefs.commute_origin_lat,
        "org_type_pref": prefs.org_type_pref,
        "weights": prefs.weights or DEFAULT_WEIGHTS,
        "ranking_mode": prefs.ranking_mode,
        "enabled_providers": prefs.enabled_providers or [],
        "keep_undisclosed_salary": prefs.keep_undisclosed_salary,
        "tile_url": prefs.tile_url or "",
        "tile_attribution": prefs.tile_attribution or "",
        "tile_crs": prefs.tile_crs or "wgs84",
        "external_searxng_url": prefs.external_searxng_url or "",
        "filter_level": prefs.filter_level or "standard",
    }


@router.put("/preferences")
def update_preferences(
    body: dict,
    db=Depends(get_db),
    token: str | None = Header(default=None, alias="X-JobMap-Token"),
):
    if not require_token(token):
        raise HTTPException(403, "缺少或错误的本地会话令牌")
    prefs = get_prefs(db)
    fields = {
        "keywords": list, "city": str, "salary_expect_min": float, "salary_expect_max": float,
        "commute_origin_lon": float, "commute_origin_lat": float, "org_type_pref": str,
        "ranking_mode": str, "enabled_providers": list, "keep_undisclosed_salary": bool,
        "tile_url": str, "tile_attribution": str, "tile_crs": str,
        "external_searxng_url": str, "filter_level": str,
    }
    for field, caster in fields.items():
        if field in body and body[field] is not None:
            try:
                value = caster(body[field]) if body[field] != "" else ("" if caster is str else None)
            except (TypeError, ValueError):
                raise HTTPException(422, f"字段 {field} 类型错误")
            setattr(prefs, field, value)
    if "weights" in body and isinstance(body["weights"], dict):
        weights = {}
        for key in ("M", "P", "C", "R", "D"):
            try:
                weights[key] = max(0.0, min(100.0, float(body["weights"].get(key, 0) or 0)))
            except (TypeError, ValueError):
                raise HTTPException(422, "权重数值错误")
        prefs.weights = weights or DEFAULT_WEIGHTS
    if prefs.ranking_mode not in RANKING_MODES:
        prefs.ranking_mode = "score"
    if prefs.tile_crs not in ("wgs84", "gcj02"):
        prefs.tile_crs = "wgs84"
    if prefs.filter_level not in ("loose", "standard", "strict"):
        prefs.filter_level = "standard"
    db.add(AuditEvent(event_type="prefs_update", detail={"fields": sorted(body.keys())}))
    db.commit()
    return read_preferences(db)


@router.post("/scores/recompute")
def recompute(token: str | None = Header(default=None, alias="X-JobMap-Token"), db=Depends(get_db)):
    if not require_token(token):
        raise HTTPException(403, "缺少或错误的本地会话令牌")
    from backend.db.seed import recompute_all_scores
    result = recompute_all_scores()
    return result
