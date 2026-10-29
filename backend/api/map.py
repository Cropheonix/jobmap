"""地图接口：返回可核实定位点。"""
from fastapi import APIRouter, Depends
from sqlalchemy import select

from backend.api.system import get_prefs
from backend.db.database import get_db
from backend.db.models import Job
from backend.api.dto import job_dto

router = APIRouter()


@router.get("/map/points")
def map_points(min_precision: str = "district", db=Depends(get_db)):
    """min_precision: building → 仅门牌/楼宇；block → 含街区/园区；district → 含行政区示意。"""
    rank = {"building": 0, "block": 1, "district": 2, "unlocatable": 3}
    threshold = rank.get(min_precision, 2)
    prefs = get_prefs(db)
    jobs = db.execute(select(Job)).scalars().unique().all()
    points = []
    for job in jobs:
        dto = job_dto(db, job, prefs.weights)
        loc = dto.get("location")
        if not loc or rank.get(loc["precision"], 3) > threshold:
            continue
        points.append({
            "job_id": dto["id"],
            "title": dto["title"],
            "org_name": dto["organization"]["name"],
            "org_type": dto["organization"]["type"],
            "category": dto["job_category"],
            "lon": loc["lon"],
            "lat": loc["lat"],
            "precision": loc["precision"],
            "precision_label": loc["precision_label"],
            "salary_display": dto["salary_display"],
            "status": dto["status"],
            "status_label": dto["status_label"],
            "city": dto["city"],
            "district": dto["district"],
            "score": dto["total_score"],
        })
    return {"points": points, "crs": "wgs84"}
