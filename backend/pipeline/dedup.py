"""去重管线：URL 规范化、公司名标准化、相似度匹配。

规则（对应方案 4.4）：
- 同一规范 URL / 同一 (provider, source_post_id) → 强重复，直接合并；
- 相同公司（标准化后）+ 职位名相似度 ≥ 0.82 + 同城同区 → 疑似重复，合并但保留全部来源。
合并后的岗位保留每个来源 URL 与观察时间，不丢弃历史证据。
"""
import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "utm_id", "spm", "from", "ref", "referrer", "share_token", "shareid",
    "chanel", "channel", "trace", "position", "src", "fr", "scene",
}

_COMPANY_SUFFIXES = [
    "股份有限公司", "有限责任公司", "有限公司", "集团公司", "集团", "事务所",
    "工作室", "设计院", "公司", "中心", "院",
]

_PUNCT_RE = re.compile(r"[\s·・，,。.、；;：:（）()\[\]【】\-—_/\\]+")


def normalize_url(url: str) -> str:
    if not url:
        return ""
    try:
        parsed = urlparse(url.strip())
    except ValueError:
        return url.strip().lower()
    scheme = parsed.scheme.lower()
    host = (parsed.hostname or "").lower()
    port = f":{parsed.port}" if parsed.port not in (None, 80, 443) else ""
    path = parsed.path.rstrip("/") or "/"
    query = [(k, v) for k, v in parse_qsl(parsed.query, keep_blank_values=True)
             if k.lower() not in TRACKING_PARAMS]
    query.sort()
    return urlunparse((scheme, host + port, path, "", urlencode(query), ""))


def normalize_company(name: str) -> str:
    text = (name or "").strip()
    text = re.sub(r"[（(][^（）()]*[)）]", "", text)  # 去掉地名后缀，如 XX（成都）
    text = _PUNCT_RE.sub("", text)
    for suffix in _COMPANY_SUFFIXES:
        if text.endswith(suffix) and len(text) > len(suffix):
            text = text[: -len(suffix)]
            break
    return text.lower()


def title_similarity(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a.strip().lower(), b.strip().lower()).ratio()


@dataclass
class DuplicationVerdict:
    kind: str            # "strong" / "suspected" / "none"
    job_id: int | None = None
    reason: str = ""


def classify(
    *,
    canonical_urls: set[str],
    provider_post_ids: set[tuple[str, str]],
    company_norm: str,
    title: str,
    city: str,
    district: str,
    existing,  # iterable of Job ORM 对象（含 sources）
) -> DuplicationVerdict:
    for job in existing:
        for src in job.sources:
            if normalize_url(src.original_url) in canonical_urls:
                return DuplicationVerdict("strong", job.id, "同一规范 URL")
            if src.provider and src.source_post_id and (src.provider, src.source_post_id) in provider_post_ids:
                return DuplicationVerdict("strong", job.id, "同源同帖 ID")
    for job in existing:
        same_company = normalize_company(job.organization.canonical_name if job.organization else "") == company_norm
        if same_company and job.city == city:
            sim = title_similarity(job.title, title)
            same_district = bool(district) and job.district == district
            if sim >= 0.82 and (same_district or sim >= 0.92):
                return DuplicationVerdict("suspected", job.id, f"同公司相似职位（相似度 {sim:.2f}）")
    return DuplicationVerdict("none")
