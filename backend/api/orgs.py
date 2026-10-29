"""机构信号接口（M2.4 信号契约）。"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select

from backend.db.database import get_db
from backend.db.models import LegalRecord, Organization, ReviewEvidence
from backend.providers.enrichment import org_signals

router = APIRouter()


@router.get("/orgs/{org_id}/signals")
def signals(org_id: int, db=Depends(get_db)):
    org = db.get(Organization, org_id)
    if not org:
        raise HTTPException(404, "机构不存在")
    return {"organization_id": org_id, "name": org.canonical_name, **org_signals(org_id)}


@router.get("/orgs/{org_id}/evidence")
def evidence(org_id: int, db=Depends(get_db)):
    org = db.get(Organization, org_id)
    if not org:
        raise HTTPException(404, "机构不存在")
    reviews = db.execute(select(ReviewEvidence).where(
        ReviewEvidence.organization_id == org_id).order_by(ReviewEvidence.id.desc()).limit(50)).scalars().all()
    legals = db.execute(select(LegalRecord).where(
        LegalRecord.organization_id == org_id).order_by(LegalRecord.id.desc()).limit(50)).scalars().all()
    return {
        "reviews": [{
            "source": r.source, "url": r.url, "title": r.title, "sentiment": r.sentiment,
            "hit_keywords": r.hit_keywords or [], "match_confidence": r.match_confidence,
            "observed_at": str(r.observed_at or ""),
        } for r in reviews],
        "legals": [{
            "record_type": l.record_type, "url": l.url, "title": l.title,
            "summary": l.summary, "filing_date": str(l.filing_date or ""),
            "match_confidence": l.match_confidence,
        } for l in legals],
    }
