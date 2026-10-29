"""查询规划器：意图 → 带预算与优先级的查询矩阵。"""
from __future__ import annotations

from dataclasses import dataclass

from backend.core.config import DEFAULT_SEARCH_ENGINES
from backend.filters.intent import Intent

# 引擎路由（对应 discovery-fetch-design.md §2：职位站走 bing/ddg，政务与贴吧走 baidu）
_JOB_SITE_ENGINE_HINTS = {
    "zhipin.com": ["bing", "duckduckgo"],
    "zhaopin.com": ["bing", "duckduckgo"],
    "51job.com": ["baidu", "bing"],
    "liepin.com": ["baidu", "bing"],
    "lagou.com": ["baidu", "bing"],
    "yupao.com": ["baidu", "bing"],
}
_GOV_ENGINE = ["baidu"]

REVIEW_SITES = ["zhihu.com", "tieba.baidu.com", "xiaohongshu.com"]
REVIEW_TERMS = ["怎么样", "避雷"]


@dataclass
class PlannedQuery:
    query: str
    engines: list[str]
    priority: int  # 越小越优先；饱和早停时从大到小跳过
    pageno: int = 1


def job_queries(intent: Intent, *, max_queries: int = 22,
                site_domains: list[str] | None = None) -> list[PlannedQuery]:
    """职位查询矩阵：基础词 × 同义词变体 × 招聘词 × 站点 site: × 引擎路由 × 翻页。"""
    role = intent.primary_role or " ".join(intent.role_terms) or "招聘"
    base = " ".join(x for x in [role, intent.city] if x)
    plans: list[PlannedQuery] = [
        PlannedQuery(base, list(DEFAULT_SEARCH_ENGINES[:3]), 0),
        PlannedQuery(base, list(DEFAULT_SEARCH_ENGINES[:3]), 0, pageno=2),   # 翻页扩量
        PlannedQuery(f"{base} 招聘", ["bing", "baidu"], 1),
        PlannedQuery(f"{base} 招聘", ["bing", "baidu"], 1, pageno=2),
        PlannedQuery(f"{base} 最新", ["bing", "baidu"], 1),
    ]
    # 同义词变体（后端→java/服务端 等扩展词单独成查询，扩充召回）
    for variant in intent.role_terms[1:4]:
        if variant != role and len(variant) >= 2:
            plans.append(PlannedQuery(f"{variant} {intent.city}".strip(), ["bing", "baidu"], 2))
    domains = site_domains or list(_JOB_SITE_ENGINE_HINTS)
    for i, domain in enumerate(domains):
        engines = _JOB_SITE_ENGINE_HINTS.get(domain, _GOV_ENGINE)
        if domain == "zhipin.com":
            # 详情页定向查询：site: 前缀限定 job_detail 路径，SERP 命中率 ~100%，
            # SERP 标题高度结构化（「岗位」_公司）；详情页为 SSR+JSON-LD（robots 允许，
            # 但站点对非浏览器 UA 有风控 → 主路径为摘要提取，抓到详情页属加分项）
            variants = [role] + [v for v in intent.role_terms
                                 if v != role and len(v) >= 2 and not v.isascii()][:3]
            seen_v = set()
            for vi, v in enumerate(variants):
                if v in seen_v:
                    continue
                seen_v.add(v)
                q = f"{v} {intent.city} site:zhipin.com/job_detail".strip()
                plans.append(PlannedQuery(q, engines, 2))
                if vi < 2:
                    plans.append(PlannedQuery(q, engines, 2, pageno=2))
            continue
        plans.append(PlannedQuery(f"{base} site:{domain}", engines, 2 + i))
        if domain == "zhaopin.com":
            plans.append(PlannedQuery(q := f"{base} site:{domain}", engines, 3 + i, pageno=2))
            plans.append(PlannedQuery(f"{role} {intent.city} 招聘 site:{domain}", engines, 3 + i))
    # 去重（同词同页只发一次）+ 截断
    seen: set[tuple[str, int]] = set()
    out: list[PlannedQuery] = []
    for p in plans:
        key = (p.query, p.pageno)
        if key in seen:
            continue
        seen.add(key)
        out.append(p)
        if len(out) >= max_queries:
            break
    return out


def gov_job_queries(intent: Intent, gov_sites: list[str]) -> list[PlannedQuery]:
    role = intent.primary_role or "招聘"
    base = " ".join(x for x in [role, intent.city] if x)
    return [PlannedQuery(f"{base} site:{site}", _GOV_ENGINE, 5) for site in gov_sites]


def review_queries(org_name: str) -> list[PlannedQuery]:
    return [PlannedQuery(f"site:{site} {org_name} {term}", ["baidu", "bing"], 0)
            for site in REVIEW_SITES for term in REVIEW_TERMS]


def legal_queries(org_name: str, region: str = "") -> list[PlannedQuery]:
    base = f"{org_name} {region}".strip()
    return [
        PlannedQuery(f"{base} 被执行人", ["baidu"], 0),
        PlannedQuery(f"{base} 劳动仲裁", ["baidu"], 1),
        PlannedQuery(f"{base} 行政处罚", ["baidu"], 2),
    ]
