"""T1 企业官网监测器（OrgWatch）：官方招聘页定期检查 + 快照对比（本周新增/下架）+ Greenhouse 官方 API。

数据分层定位（用户架构方案）：
- T1 直取层是长期职位库的主基础（允许保存的企业自有公开数据）；
- 官网招聘页变化 = 最高可信度的在招/下架证据（区别于搜索索引线索）；
- Greenhouse/Lever/Ashby 是企业官方提供的公开招聘接口，无需认证、允许使用。
"""
from __future__ import annotations

import hashlib
import re
from datetime import date

import httpx
from sqlalchemy import select

from backend.db.database import db_scope
from backend.db.models import AuditEvent, DomainAccessState, Organization, SiteSnapshot

# 招聘链接特征（通用提取：官网/招聘板块中的职位详情链接）
_JOB_LINK_HINTS = re.compile(r"(job|career|hire|zhaopin|recruit|joinus|join-us|position)", re.I)
_JOB_LINK_RE = re.compile(r'href="([^"]{5,300})"', re.I)
# 静态资源后缀排除（_nuxt/xxx.css 之类会被 job 关键词误命中）
_ASSET_SUFFIX = (".css", ".js", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico",
                 ".woff", ".woff2", ".ttf", ".webp", ".map", ".xml", ".json")
_TEXT_DENSITY_HINTS = ("招聘", "岗位", "职位", "加入我们", "join us", "careers")


def _extract_watch_links(html: str, page_url: str) -> list[str]:
    """从官网招聘页提取疑似职位链接（仅绝对化，不递归）。"""
    from urllib.parse import urljoin, urlparse

    netloc = urlparse(page_url).netloc
    out, seen = [], set()
    for m in _JOB_LINK_RE.finditer(html or ""):
        href = m.group(1)
        if href.startswith(("javascript:", "#", "mailto:", "tel:")):
            continue
        if href.lower().split("?")[0].endswith(_ASSET_SUFFIX):
            continue
        if not _JOB_LINK_HINTS.search(href):
            continue
        url = urljoin(page_url, href).split("#")[0]
        if urlparse(url).netloc != netloc:
            continue
        # 排除与当前页同路径、仅 query 不同的筛选变体（如 ?city=上海）
        if url.split("?")[0].rstrip("/") == page_url.split("?")[0].rstrip("/") and "?" in url:
            continue
        if url not in seen:
            seen.add(url)
            out.append(url)
    return out[:40]


def check_organization(org_id: int) -> dict:
    """检查单个机构的官方招聘页/ATS，快照对比输出 added/removed。"""
    result = {"org_id": org_id, "checked": [], "added": [], "removed": [], "errors": []}
    with db_scope() as db:
        org = db.get(Organization, org_id)
        if not org:
            result["errors"].append("机构不存在")
            return result
        name = org.canonical_name
        targets = []
        if org.ats_board:
            targets.append(("ats", org.ats_board))
        for label, url in (("page", org.recruiting_page), ("site", org.official_site if org.recruiting_page == "" else None)):
            if url:
                targets.append((label, url))

    today = date.today()
    for kind, target in targets:
        try:
            if kind == "ats":
                links, titles = _fetch_ats_board(target)
                source = target
            else:
                links, titles = _fetch_page_links(target)
                source = target
        except Exception as exc:
            result["errors"].append(f"{kind}:{exc}"[:200])
            continue
        result["checked"].append(source)

        with db_scope() as db:
            snap = db.execute(select(SiteSnapshot).where(
                SiteSnapshot.organization_id == org_id,
                SiteSnapshot.url == source)).scalar_one_or_none()
            old_links = set(snap.links or []) if snap else set()
            new_links = set(links)
            added = sorted(new_links - old_links)
            removed = sorted(old_links - new_links)
            links_hash = hashlib.sha256("|".join(sorted(new_links)).encode()).hexdigest()[:16]
            if not snap:
                snap = SiteSnapshot(organization_id=org_id, url=source)
                db.add(snap)
            snap.links = sorted(new_links)
            snap.links_hash = links_hash
            snap.added = added
            snap.removed = removed
            snap.checked_at = date.today()
            if added or removed:
                db.add(AuditEvent(event_type="watch_change", record_id=str(org_id),
                                  detail={"source": source[:200], "added": added[:20],
                                          "removed": removed[:20]}))
            result["added"].extend(added)
            result["removed"].extend(removed)
            # 新增链接作为最高可信度记录入库（official_site 源）
            _ingest_watch_links(org_id, name, added, titles, today)
    return result


def _ingest_watch_links(org_id: int, org_name: str, links: list[str], titles: dict, today: date) -> None:
    from backend.filters import intent as intent_mod
    from backend.filters.pipeline import run_filters
    from backend.pipeline.extractor import build_record
    from backend.pipeline.loader import upsert_job_record

    for url in links[:15]:
        html = ""
        try:
            with httpx.Client(timeout=10, follow_redirects=True,
                              headers={"User-Agent": "JobMap/1.0 (+local watch)"}) as c:
                resp = c.get(url)
                if resp.status_code == 200:
                    html = resp.text[:262144]
        except httpx.HTTPError:
            html = ""
        record = build_record(html, url, {"title": titles.get(url, ""), "snippet": ""})
        if not record.get("organization", {}).get("name"):
            record["organization"] = {"name": org_name}
        record["sources"] = [{
            "provider": "企业官网监测", "url": url, "source_type": "official_site",
            "access_status": "accessible" if html else "pending",
            "observed_at": today.isoformat(),
            "evidence_excerpt": "官网招聘页快照对比发现的新链接",
            "license_note": "企业自有公开数据，允许保存",
        }]
        record["discovered_via"] = "org_watch"
        record["is_demo"] = False
        intent = intent_mod.parse_intent(record.get("title") or "招聘")
        result = run_filters(record, intent, today=today)
        if result.zone == "quarantined" and result.rejection_reason == "missing_required":
            continue
        try:
            with db_scope() as db:
                outcome, job, _why = upsert_job_record(db, record)
                if job is not None:
                    job.filter_zone = result.zone if result.zone != "quarantined" else "accepted_suspect"
                    job.confidence = result.confidence
                    job.rejection_reason = result.rejection_reason or ""
                    job.filter_trace = result.to_trace()
                    job.discovered_via = "org_watch"
        except Exception:
            pass


def _fetch_page_links(url: str) -> tuple[list[str], dict]:
    with httpx.Client(timeout=12, follow_redirects=True,
                      headers={"User-Agent": "JobMap/1.0 (+local watch)"}) as c:
        resp = c.get(url)
        resp.raise_for_status()
        html = resp.text[:524288]
    links = _extract_watch_links(html, url)
    return links, {}


def _fetch_ats_board(spec: str) -> tuple[list[str], dict]:
    """Greenhouse 官方公开招聘接口（无需认证）：ats_board = greenhouse:<board_token>。"""
    provider, _, token = spec.partition(":")
    if provider != "greenhouse":
        raise ValueError(f"暂不支持的 ATS：{provider}")
    with httpx.Client(timeout=12, trust_env=False) as c:
        resp = c.get(f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs")
        resp.raise_for_status()
        data = resp.json()
    titles = {}
    links = []
    for job in data.get("jobs", []):
        url = job.get("absolute_url") or ""
        if url:
            links.append(url)
            titles[url] = job.get("title") or ""
    return links, titles


def run_watch(limit: int = 50) -> dict:
    """对所有配置了招聘页/ATS 的机构执行监测。"""
    with db_scope() as db:
        orgs = db.execute(select(Organization).where(
            (Organization.recruiting_page != "") | (Organization.ats_board != ""))).scalars().all()
        all_ids = [o.id for o in orgs][:limit]
    summary = {"orgs": 0, "added": 0, "removed": 0, "errors": 0, "details": []}
    for oid in all_ids:
        r = check_organization(oid)
        summary["orgs"] += 1
        summary["added"] += len(r["added"])
        summary["removed"] += len(r["removed"])
        summary["errors"] += len(r["errors"])
        summary["details"].append(r)
    return summary


# ── T3 域名降级状态（探测一次撞墙即永久走索引线索） ─────────
def domain_downgraded(domain: str) -> bool:
    with db_scope() as db:
        row = db.get(DomainAccessState, domain)
        return bool(row and row.state == "downgraded")


def mark_domain_downgraded(domain: str, feature: str) -> None:
    with db_scope() as db:
        row = db.get(DomainAccessState, domain)
        if not row:
            row = DomainAccessState(domain=domain)
            db.add(row)
        row.state = "downgraded"
        row.feature = feature[:200]
        row.probe_count = (row.probe_count or 0) + 1
        db.add(AuditEvent(event_type="domain_downgraded", record_id=domain,
                          detail={"feature": feature[:200]}))
