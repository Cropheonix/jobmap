"""系统接口：会话配置、数据源清单、运行日志。"""
from fastapi import APIRouter, Depends
from sqlalchemy import select

from backend.core.config import (
    ACTION_STATUSES,
    ALGORITHM_VERSION,
    APP_NAME,
    APP_VERSION,
    DEFAULT_TILE_ATTRIBUTION,
    DEFAULT_TILE_URL,
    DEFAULT_WEIGHTS,
    JOB_CATEGORIES,
    JOB_STATUS,
    LOCATION_PRECISION,
    ORG_TYPES,
    RANKING_MODES,
    SOURCE_TYPES,
    WEIGHT_LABELS,
)
from backend.core.security import SESSION_TOKEN
from backend.db.database import get_db
from backend.db.models import AuditEvent, UserPreference
from backend.providers.registry import list_providers

router = APIRouter()


def get_prefs(db) -> UserPreference:
    prefs = db.get(UserPreference, 1)
    if not prefs:
        prefs = UserPreference(id=1, weights=DEFAULT_WEIGHTS,
                               enabled_providers=["sample", "user_import"])
        db.add(prefs)
        db.commit()
    return prefs


@router.get("/session/config")
def session_config(db=Depends(get_db)):
    prefs = get_prefs(db)
    return {
        "app": APP_NAME,
        "version": APP_VERSION,
        "algorithm_version": ALGORITHM_VERSION,
        "mode": "本地模式（数据仅存于本机）",
        "token": SESSION_TOKEN,
        "tile": {
            "url": prefs.tile_url or DEFAULT_TILE_URL,
            "attribution": prefs.tile_attribution or DEFAULT_TILE_ATTRIBUTION,
            "crs": prefs.tile_crs or "wgs84",
        },
        "enums": {
            "job_categories": JOB_CATEGORIES,
            "org_types": ORG_TYPES,
            "statuses": JOB_STATUS,
            "precisions": LOCATION_PRECISION,
            "actions": ACTION_STATUSES,
            "ranking_modes": RANKING_MODES,
            "source_types": SOURCE_TYPES,
            "weight_labels": WEIGHT_LABELS,
        },
        "offline_note": "离线时地图底图不可用，列表/筛选/导出不受影响。",
    }


@router.get("/providers")
def providers():
    return {"providers": list_providers()}


@router.get("/logs")
def logs(limit: int = 50, db=Depends(get_db)):
    rows = db.execute(
        select(AuditEvent).order_by(AuditEvent.timestamp.desc()).limit(min(limit, 200))
    ).scalars().all()
    return {
        "logs": [
            {"id": r.id, "event_type": r.event_type, "record_id": r.record_id,
             "timestamp": r.timestamp.isoformat(), "detail": r.detail}
            for r in rows
        ]
    }
