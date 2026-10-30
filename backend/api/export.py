"""导出（CSV/JSON/HTML）与导入（JSON/CSV）。"""
import csv
import io
import json
from datetime import datetime

from fastapi import APIRouter, Depends, File, Header, HTTPException, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from sqlalchemy import select

from backend.api.dto import job_dto
from backend.api.jobs import list_jobs
from backend.api.system import get_prefs
from backend.core.config import ALGORITHM_VERSION, APP_NAME, WEIGHT_LABELS
from backend.core.security import require_token, sanitize_csv_cell
from backend.db.database import db_scope, get_db
from backend.db.models import AuditEvent, Job
from backend.pipeline.loader import upsert_job_record

router = APIRouter()

_CSV_COLUMNS = [
    "排名", "公司", "机构类型", "职位", "行业类别", "城市", "行政区", "详细地址",
    "薪资下限(K/月)", "薪资上限(K/月)", "薪资月数", "经验", "学历", "发布日期", "最近核查",
    "状态", "综合分", "证据完整度(%)", "缺失字段",
    "岗位匹配(M)", "薪资(P)", "团队(C)", "可信度(R)", "通勤(D)",
    "来源数", "来源1", "来源2", "来源3", "演示数据",
]


def _filtered_jobs(db, params: dict):
    result = list_jobs(db=db, **params)
    return result


@router.get("/export")
def export_data(
    format: str = "csv",
    q: str | None = None, city: str | None = None, district: str | None = None,
    category: str | None = None, status: str | None = None, source_type: str | None = None,
    salary_min: float | None = None, salary_undisclosed: str = "keep",
    order: str = "score", db=Depends(get_db),
):
    params = dict(q=q, city=city, district=district, category=category, status=status,
                  source_type=source_type, salary_min=salary_min,
                  salary_undisclosed=salary_undisclosed, order=order, limit=2000)
    data = _filtered_jobs(db, params)
    items = data["items"]
    prefs = get_prefs(db)
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    base_meta = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "algorithm_version": ALGORITHM_VERSION,
        "weights": prefs.weights,
        "weight_labels": WEIGHT_LABELS,
        "filters": {k: v for k, v in params.items() if v},
        "stats": {"total": len(items), "demo": sum(1 for i in items if i["is_demo"])},
    }
    with db_scope() as audit_db:
        audit_db.add(AuditEvent(event_type="export", detail={"format": format, "total": len(items)}))

    if format == "json":
        payload = {**base_meta, "jobs": items}
        return JSONResponse(
            payload,
            headers={"Content-Disposition": f'attachment; filename="jobmap_export_{stamp}.json"'},
        )

    if format == "html":
        from backend.exports.html_export import render_report
        html = render_report({**base_meta, "jobs": items})
        return HTMLResponse(
            html,
            headers={"Content-Disposition": f'attachment; filename="jobmap_report_{stamp}.html"'},
        )

    # CSV（UTF-8-SIG，Excel/WPS 中文无乱码；单元格做公式注入防护）
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(_CSV_COLUMNS)
    for idx, item in enumerate(items, 1):
        subs = item.get("subscores") or {}
        sources = item.get("sources") or []
        writer.writerow([
            sanitize_csv_cell(idx),
            sanitize_csv_cell(item["organization"]["name"]),
            sanitize_csv_cell(item["organization"]["type_label"]),
            sanitize_csv_cell(item["title"]),
            sanitize_csv_cell(item["job_category"]),
            sanitize_csv_cell(item["city"]),
            sanitize_csv_cell(item["district"]),
            sanitize_csv_cell(item["address"]),
            sanitize_csv_cell(item["salary_min"] if item["salary_min"] is not None else ""),
            sanitize_csv_cell(item["salary_max"] if item["salary_max"] is not None else ""),
            sanitize_csv_cell(item["salary_months"] or ""),
            sanitize_csv_cell(item["experience"]),
            sanitize_csv_cell(item["education"]),
            sanitize_csv_cell(item["published_at"] or ""),
            sanitize_csv_cell(item["last_checked"] or ""),
            sanitize_csv_cell(item["status_label"]),
            sanitize_csv_cell(item["total_score"] if item["total_score"] is not None else ""),
            sanitize_csv_cell(item["completeness"] if item["completeness"] is not None else ""),
            sanitize_csv_cell("、".join(item["missing_fields"])),
            *[sanitize_csv_cell(subs.get(k, "")) for k in ("M", "P", "C", "R", "D")],
            sanitize_csv_cell(item["sources_count"]),
            *[sanitize_csv_cell(s["url"]) for s in sources[:3]],
            sanitize_csv_cell("是" if item["is_demo"] else "否"),
        ])
    output = buf.getvalue().encode("utf-8-sig")
    return StreamingResponse(
        io.BytesIO(output),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="jobmap_export_{stamp}.csv"'},
    )


@router.post("/import")
async def import_data(
    file: UploadFile | None = File(default=None),
    db=Depends(get_db),
    token: str | None = Header(default=None, alias="X-JobMap-Token"),
):
    if not require_token(token):
        raise HTTPException(403, "缺少或错误的本地会话令牌")
    records: list[dict] = []
    if file is not None:
        raw = await file.read()
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            raise HTTPException(422, "文件编码需为 UTF-8")
        if (file.filename or "").lower().endswith(".json"):
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError as exc:
                raise HTTPException(422, f"JSON 解析失败：{exc}")
            records = parsed if isinstance(parsed, list) else parsed.get("jobs") or []
        else:  # CSV
            records = _parse_csv_jobs(text)
    else:
        raise HTTPException(422, "请上传 JSON 或 CSV 文件")

    result = {"added": 0, "merged": 0, "rejected": 0, "errors": []}
    with db_scope() as wdb:
        for record in records:
            try:
                outcome, job, why = upsert_job_record(wdb, record)
                key = {"created": "added", "merged": "merged", "rejected": "rejected"}[outcome]
                result[key] += 1
                if outcome == "rejected":
                    result["errors"].append(why)
            except Exception as exc:
                result["rejected"] += 1
                result["errors"].append(str(exc))
        wdb.add(AuditEvent(event_type="import", detail={k: v for k, v in result.items() if k != "errors"}))
    from backend.db.seed import recompute_all_scores
    recompute_all_scores()
    return result


@router.post("/import/text")
def import_text(
    body: dict,
    db=Depends(get_db),
    token: str | None = Header(default=None, alias="X-JobMap-Token"),
):
    """T4 用户辅助导入：粘贴职位文本/URL/HTML 片段（用户自行复制允许使用的内容）。

    - 单行 http(s) URL：robots 允许则抓一次提取，否则登记为待核查线索；
    - 文本块：走正则提取链（岗位/薪资/经验/公司后缀启发），缺失字段留空由用户在界面确认。
    """
    if not require_token(token):
        raise HTTPException(403, "缺少或错误的本地会话令牌")
    text = (body.get("text") or "").strip()
    if not text:
        raise HTTPException(422, "内容为空")
    from datetime import date

    from backend.filters import intent as intent_mod
    from backend.filters.pipeline import run_filters
    from backend.pipeline.extractor import build_record
    from backend.pipeline.loader import upsert_job_record
    from backend.core.security import validate_public_http_url

    url = ""
    html = ""
    if "\n" not in text and text.startswith(("http://", "https://")):
        ok, reason = validate_public_http_url(text)
        if not ok:
            raise HTTPException(422, f"URL 校验失败：{reason}")
        url = text
        html = ""
        try:
            import httpx
            with httpx.Client(timeout=12, follow_redirects=True,
                              headers={"User-Agent": "JobMap/1.0 (+user paste)"}) as c:
                resp = c.get(url)
                if resp.status_code == 200:
                    html = resp.text[:262144]
        except Exception:
            html = ""
        record = build_record(html, url, {"title": "", "snippet": ""})
        if not html:
            record["extraction_tier"] = "snippet"
            record["description_summary"] = "用户粘贴的 URL，原文未抓取（待核查）"
    else:
        lines = [l.strip() for l in text.splitlines() if l.strip()]
        record = build_record("", "https://import.jobmap.local/paste", {
            "title": lines[0][:120] if lines else "",
            "snippet": " ".join(lines[1:])[:600],
        })
        record["sources"] = [{
            "provider": "用户粘贴", "url": url or "https://import.jobmap.local/paste",
            "source_type": "user_import", "access_status": "pending",
            "observed_at": str(date.today()),
            "evidence_excerpt": text[:200],
            "license_note": "用户自行复制导入，由用户负责来源合法性",
        }]
    if not record.get("sources"):
        record["sources"] = [{
            "provider": "用户粘贴", "url": url, "source_type": "user_import",
            "access_status": "accessible" if html else "pending",
            "observed_at": str(date.today()), "evidence_excerpt": text[:200],
            "license_note": "用户自行复制导入，由用户负责来源合法性",
        }]
    record["is_demo"] = False
    record["discovered_via"] = "user_paste"
    record.setdefault("city", (body.get("city") or ""))

    from backend.api.system import get_prefs
    prefs = get_prefs(db)
    user_intent = intent_mod.parse_intent(" ".join(prefs.keywords or []) + " " + (prefs.city or ""))
    result = run_filters(record, user_intent, today=date.today())
    outcome, job, why = upsert_job_record(db, record)
    if job is not None:
        job.filter_zone = result.zone if result.zone != "quarantined" else "accepted_suspect"
        job.confidence = result.confidence
        job.rejection_reason = result.rejection_reason or ""
        job.filter_trace = result.to_trace()
        job.discovered_via = "user_paste"
    db.add(AuditEvent(event_type="import_text", detail={"outcome": outcome, "url": url[:200]}))
    db.commit()
    return {"result": outcome, "reason": why, "job_id": job.id if job else None,
            "missing_fields": result.to_trace().get("flags", [])}


def _parse_csv_jobs(text: str) -> list[dict]:
    reader = csv.DictReader(io.StringIO(text))
    records = []
    for row in reader:
        row = {(k or "").strip(): (v or "").strip() for k, v in row.items() if k}
        if not row.get("title") or not (row.get("company") or row.get("organization_name")):
            continue
        record = {
            "organization": {
                "name": row.get("company") or row.get("organization_name"),
                "type": row.get("org_type") or "unknown",
                "industry": row.get("industry") or "",
            },
            "title": row["title"],
            "description_summary": row.get("description") or "",
            "city": row.get("city") or "",
            "district": row.get("district") or "",
            "address": row.get("address") or "",
            "salary_min": float(row["salary_min"]) if row.get("salary_min") else None,
            "salary_max": float(row["salary_max"]) if row.get("salary_max") else None,
            "salary_months": int(row["salary_months"]) if row.get("salary_months") else None,
            "experience": row.get("experience") or "不限",
            "education": row.get("education") or "",
            "job_category": row.get("job_category") or "其他行业",
            "keywords": [k for k in (row.get("keywords") or "").replace("；", ";").split(";") if k],
            "published_at": row.get("published_at") or None,
            "last_checked": row.get("last_checked") or None,
            "status": row.get("status") or "unverified",
            "keywords_hint": "",
            "sources": [{
                "provider": row.get("source_provider") or "用户导入CSV",
                "url": row.get("url") or "https://example.com/import/pending",
                "source_type": "user_import",
                "observed_at": row.get("observed_at") or None,
            }],
            "is_demo": False,
        }
        loc_lon = row.get("lon")
        loc_lat = row.get("lat")
        if loc_lon and loc_lat:
            record["location"] = {"lon": float(loc_lon), "lat": float(loc_lat),
                                  "crs": row.get("crs") or "wgs84",
                                  "precision": row.get("precision") or "district"}
        records.append(record)
    return records
