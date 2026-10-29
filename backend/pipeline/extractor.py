"""关键信息提取：JSON-LD(JobPosting) → 站点/正则解析 → Trafilatura 通用兜底。"""
from __future__ import annotations

import json
import re
from datetime import date, datetime
from urllib.parse import urlparse

try:  # 可选依赖，缺失时降级为纯正则
    import trafilatura  # type: ignore
    HAS_TRAFILATURA = True
except ImportError:  # pragma: no cover
    trafilatura = None
    HAS_TRAFILATURA = False

_SALARY_RE = re.compile(r"(\d{1,3})\s*[-–~至]\s*(\d{1,3})\s*[kK]")
_MONTHS_RE = re.compile(r"[·x×*]\s*(\d{1,2})\s*薪")
_EXP_RE = re.compile(r"(应届|不限|在校生|\d+\s*[-–~]\s*\d+\s*年|\d+年以上)")
_DATE_RE = re.compile(r"(20\d{2})[-/.年](\d{1,2})[-/.月](\d{1,2})")
_JSONLD_RE = re.compile(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>',
                        re.DOTALL | re.IGNORECASE)

REGION_ALIASES = {"四川": "成都", "浙江": "杭州", "广东": "深圳"}


_JOB_LINK_RES = [
    re.compile(r'href="(/job_detail/[A-Za-z0-9_~]+\.html)"'),        # BOSS直聘
    re.compile(r'href="(https?://www\.zhipin\.com/job_detail/[A-Za-z0-9_~]+\.html)"'),
    re.compile(r'href="(/jobs/[A-Za-z0-9_]+/?)"'),                    # 智联
]


def extract_job_links(html: str, page_url: str) -> list[str]:
    """从列表页 HTML 提取职位详情链接（一轮展开，仅绝对化路径不递归）。"""
    base = page_url.rsplit("/", 1)[0]
    found: list[str] = []
    seen: set[str] = set()
    for rx in _JOB_LINK_RES:
        for m in rx.finditer(html or ""):
            href = m.group(1)
            if href.startswith("http"):
                url = href.split("?")[0]
            else:
                url = (base + href if not href.startswith("/") else
                       f"https://{urlparse(page_url).netloc}{href}").split("?")[0]
            if url not in seen:
                seen.add(url)
                found.append(url)
    return found


def extract_jsonld(html: str) -> dict | None:
    """提取 schema.org JobPosting JSON-LD，映射为记录字段；无则返回 None。"""
    for match in _JSONLD_RE.finditer(html):
        try:
            data = json.loads(match.group(1).strip())
        except (json.JSONDecodeError, ValueError):
            continue
        for node in (data if isinstance(data, list) else [data]):
            if not isinstance(node, dict):
                continue
            graph = node.get("@graph") if isinstance(node.get("@graph"), list) else [node]
            for item in graph:
                if not isinstance(item, dict):
                    continue
                types = item.get("@type") or []
                types = types if isinstance(types, list) else [types]
                if "JobPosting" not in types:
                    continue
                return _map_jobposting(item)
    return None


def _map_jobposting(item: dict) -> dict:
    org = item.get("hiringOrganization") or {}
    org_name = org.get("name") or (org.get("legalName") if isinstance(org, dict) else "")
    location = item.get("jobLocation") or {}
    if isinstance(location, list):
        location = location[0] if location else {}
    address = location.get("address") or {}
    city = address.get("addressLocality") or ""
    city = REGION_ALIASES.get(city, city)
    district = address.get("addressRegion") or ""
    salary = item.get("baseSalary") or {}
    value = salary.get("value") or salary
    smin = value.get("minValue") if isinstance(value, dict) else None
    smax = value.get("maxValue") if isinstance(value, dict) else None
    unit = (value.get("unitText") if isinstance(value, dict) else "") or ""
    if isinstance(value, dict) and value.get("value") is not None and not (smin or smax):
        smax = value.get("value")
    if "YEAR" in unit.upper() and (smin or smax):  # 年薪转月薪（千/月）
        smin = round(smin / 12, 1) if smin else None
        smax = round(smax / 12, 1) if smax else None
    description = re.sub(r"<[^>]+>", " ", str(item.get("description") or ""))[:600]
    return {
        "title": str(item.get("title") or "").strip(),
        "organization": {"name": str(org_name or "").strip()},
        "city": str(city).replace("市", ""),
        "district": "" if district in (city, "四川", "中国") else str(district).replace("区", "区"),
        "address": str(address.get("streetAddress") or "")[:200],
        "salary_min": float(smin) if smin is not None else None,
        "salary_max": float(smax) if smax is not None else None,
        "published_at": _iso_date(item.get("datePosted")),
        "valid_through": _iso_date(item.get("validThrough")),
        "description_summary": description.strip(),
        "extraction_tier": "jsonld",
    }


def _iso_date(value) -> str | None:
    if not value:
        return None
    try:
        return datetime.strptime(str(value)[:10], "%Y-%m-%d").date().isoformat()
    except ValueError:
        return None


def extract_by_regex(text: str) -> dict:
    """正则字段兜底（标题+摘要+正文）。"""
    out: dict = {"extraction_tier": "text"}
    m = _SALARY_RE.search(text)
    if m:
        out["salary_min"], out["salary_max"] = float(m.group(1)), float(m.group(2))
        mm = _MONTHS_RE.search(text)
        if mm:
            out["salary_months"] = int(mm.group(1))
    m = _EXP_RE.search(text)
    if m:
        out["experience"] = m.group(1)
    m = _DATE_RE.search(text)
    if m:
        try:
            out["published_at"] = date(int(m.group(1)), int(m.group(2)), int(m.group(3))).isoformat()
        except ValueError:
            pass
    return out


def extract_main_text(html: str) -> str:
    """Trafilatura 正文兜底；缺失依赖时退化为去标签文本。"""
    if HAS_TRAFILATURA:
        text = trafilatura.extract(html, include_comments=False) or ""
        if text:
            return text[:4000]
    return re.sub(r"<[^>]+>", " ", html)[:4000]


_ZHIPIN_SERP_PATTERNS = [
    # 「岗位招聘」_公司名招聘 - BOSS直聘 / {岗位}招聘_{公司} - BOSS直聘
    re.compile(r"[「“]?.{0,4}(?P<role>[^「」_]{2,25})(?:招聘)?[」”]?\s*_\s*(?P<org>[^-_「」]{2,25}?)\s*(?:公司)?招聘"),
    # {岗位}是什么职位 - BOSS直聘（问答页，标记）
    re.compile(r"(?P<qa>.{2,25})是什么职位"),
    # {公司}{年份}年{岗位}工作要求/岗位职责
    re.compile(r"(?P<org>[^0-9「」-]{2,25}?)20\d{2}年(?P<role>.{2,25}?)(?:工作|岗位)"),
]


def parse_zhipin_serp_title(title: str) -> dict:
    """解析 BOSS直聘 SERP 标题结构：提取岗位名/公司名；问答页返回 is_qa 标记。"""
    out: dict = {}
    t = (title or "").strip()
    m = _ZHIPIN_SERP_PATTERNS[1].search(t)
    if m:
        out["is_qa"] = True
        out["title"] = m.group(1)
        return out
    m = _ZHIPIN_SERP_PATTERNS[0].search(t)
    if m:
        out["title"] = m.group("role").strip("「」“”招聘 ")
        out["organization"] = {"name": m.group("org").strip("「」“” ")}
        return out
    m = _ZHIPIN_SERP_PATTERNS[2].search(t)
    if m:
        out["organization"] = {"name": m.group("org").strip()}
        out["title"] = m.group("role").strip()
        return out
    return out


def build_record(html: str, url: str, hit: dict | None = None) -> dict:
    """组合提取链，输出规范化记录（供筛选与入库）。

    hit: {title, snippet, engine, site, published_date}——SERP 摘要作为兜底证据与字段来源。
    """
    hit = hit or {}
    title = hit.get("title") or ""
    snippet = hit.get("snippet") or ""
    record: dict = {"extraction_tier": "snippet"}

    jsonld = extract_jsonld(html) if html else None
    if jsonld:
        record.update(jsonld)
        record["extraction_tier"] = "jsonld"
    elif html:
        main_text = extract_main_text(html)
        blob = " ".join([title, snippet, main_text])
        record.update({
            "title": _clean_title(title),
            "description_summary": (snippet or main_text)[:600],
        })
        record.update(extract_by_regex(blob))
        record["extraction_tier"] = "text"
    else:
        # 无 HTML（未抓取）：SERP 摘要即证据
        record.update({
            "title": _clean_title(title),
            "description_summary": snippet[:600],
        })
        record.update(extract_by_regex(f"{title} {snippet}"))
        record["extraction_tier"] = "snippet"

    if not record.get("title"):
        record["title"] = _clean_title(title)
    if not record.get("published_at") and hit.get("published_date"):
        record["published_at"] = _iso_date(hit["published_date"])

    record.setdefault("organization", {})
    org = record.get("organization") or {}
    if not org.get("name"):
        # 从标题/摘要提取公司名启发：公司后缀词
        m = re.search(r"(.{2,20}?)(?:有限公司|股份有限公司|有限责任公司|集团公司|事务所|工作室)", title + " " + snippet)
        if m:
            record["organization"] = {"name": m.group(1)[-30:]}
    # BOSS直聘 SERP 标题结构化解析（覆盖公司名提取盲区；问答页标记后由筛选处置）
    if "zhipin.com" in url:
        parsed = parse_zhipin_serp_title(record.get("title") or "")
        if parsed.get("is_qa"):
            record["status_hint"] = (record.get("status_hint") or "") + " 问答页"
            record["is_qa_page"] = True
        else:
            if parsed.get("organization", {}).get("name") and not record["organization"].get("name"):
                record["organization"] = parsed["organization"]
            if parsed.get("title") and len(parsed["title"]) >= 2:
                record["title"] = parsed["title"] + ("工程师" if "工程师" in (record.get("title") or "") and "工程师" not in parsed["title"] else "")
    return record


def _clean_title(title: str) -> str:
    t = re.sub(r"[【】\[\]]", " ", title or "")
    return re.sub(r"\s+", " ", t).strip()[:120]
