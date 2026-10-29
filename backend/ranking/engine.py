"""评分引擎 v1（对应方案第 5 节）。

两层结构：
1. 过滤与状态门控：由 API 层根据用户筛选完成；已截止/下架岗位默认不进入「可投递」榜单；
2. 可解释综合分（0-100）：Score = Σ w_i·s_i / Σ w_i，
   s = {M 岗位匹配, P 薪资, C 团队与项目, R 来源可信度与时效, D 通勤}。

原则：
- 缺失 ≠ 低分：缺失维度取中性分 50 并记入 missing_fields，绝不伪造数值；
- R 是「信息可靠程度估计」，不是「保证在招」的认证；
- page_accessible 与 actively_hiring 严格区分（见状态字段）。
"""
from __future__ import annotations

from datetime import date

from backend.core.config import ALGORITHM_VERSION, DEFAULT_WEIGHTS, MISSING_NEUTRAL_SCORE
from backend.pipeline.coords import haversine_km

# ---- 关键词工具 -----------------------------------------------------------


def tokenize_keywords(keywords) -> list[str]:
    if not keywords:
        return []
    if isinstance(keywords, str):
        raw = keywords.replace("，", " ").replace(",", " ").replace("、", " ").split()
    else:
        raw = []
        for k in keywords:
            raw.extend(str(k).replace("，", " ").replace(",", " ").replace("、", " ").split())
    seen, tokens = set(), []
    for t in raw:
        t = t.strip().lower()
        if t and t not in seen:
            seen.add(t)
            tokens.append(t)
    return tokens


def _match_ratio(tokens: list[str], *texts: str) -> tuple[float, int]:
    """返回 (命中比例 0-1, 命中数)。空文本按未命中计。"""
    if not tokens:
        return 0.0, 0
    blob = " ".join(texts or "").lower()
    hits = sum(1 for t in tokens if t in blob)
    return hits / len(tokens), hits


# ---- 五个维度 -------------------------------------------------------------


def compute_m(job, org, tokens) -> tuple[float, dict]:
    """岗位匹配度：标题 35 / 职责正文 35 / 行业与项目类型 20 / 技能要求 10。"""
    if not tokens:
        return MISSING_NEUTRAL_SCORE, {"note": "未设置关键词，取中性分"}
    title_ratio, _ = _match_ratio(tokens, job.title)
    desc_ratio, _ = _match_ratio(tokens, job.description_summary, " ".join(job.keywords or []))
    ind_ratio, _ = _match_ratio(tokens, job.job_category, job.industry, org.industry if org else "")
    skill_ratio, _ = _match_ratio(tokens, " ".join(job.keywords or []), job.title)
    score = 100 * (0.35 * title_ratio + 0.35 * desc_ratio + 0.20 * ind_ratio + 0.10 * skill_ratio)
    return round(min(100, max(0, score)), 1), {
        "标题命中": f"{title_ratio:.0%}", "职责命中": f"{desc_ratio:.0%}",
        "行业命中": f"{ind_ratio:.0%}", "技能命中": f"{skill_ratio:.0%}",
    }


def compute_p(job, prefs) -> tuple[float, dict, bool]:
    """薪资匹配：岗位公开区间 vs 用户期望区间（千元/月）。缺失取 50 并标记。"""
    missing = job.salary_max is None or job.salary_min is None or (job.salary_max or 0) <= 0
    if missing:
        return MISSING_NEUTRAL_SCORE, {"note": "薪资未公开，评分不完整（取中性分）"}, True
    a, b = prefs.salary_expect_min, prefs.salary_expect_max
    if not a or not b:
        return MISSING_NEUTRAL_SCORE, {"note": "未设置期望薪资，取中性分"}, False
    c, d = float(job.salary_min), float(job.salary_max)
    overlap = max(0.0, min(b, d) - max(a, c))
    if overlap > 0:
        inner = overlap / max(1e-6, min(d - c, b - a))
        score = 70 + 30 * inner
        note = "与期望区间有重叠"
    else:
        gap = max(a - d, c - b)
        span = max(b - a, 1.0)
        score = max(20.0, 50.0 - 30.0 * gap / span)
        note = "与期望区间无重叠"
    if job.salary_months and job.salary_months > 13:
        score = min(100.0, score + 5)
        note += f"；{job.salary_months} 薪有加成"
    return round(score, 1), {"期望区间": f"{a:g}-{b:g}K", "岗位区间": f"{c:g}-{d:g}K", "note": note}, False


_SIZE_STABILITY = {"large": 80, "medium": 70, "small": 60}
_SIZE_BAND_MAP = [
    (("1000", "5000", "万人"), "large"), (("200", "500", "900"), "medium"), (("50", "100", "150"), "small"),
]


def _size_to_band(size_band: str) -> str:
    text = size_band or ""
    for keys, band in _SIZE_BAND_MAP:
        if any(k in text for k in keys):
            return band
    return "unknown"


def compute_c(job, org, prefs, tokens) -> tuple[float, dict]:
    """团队与项目适配：行业契合 30 / 可核实代表项目 25 / 成长路径 20 / 稳定性 15 / 机构偏好 10。

    注意：不以规模天然偏大公司；稳定性只是启发式估计并披露。
    """
    parts = {}
    if tokens:
        ind_ratio, _ = _match_ratio(tokens, job.industry, org.industry if org else "", job.job_category)
        parts["行业契合(30)"] = 100 * ind_ratio
    else:
        parts["行业契合(30)"] = MISSING_NEUTRAL_SCORE
    projects = (org.verified_projects if org else None) or []
    parts["代表项目(25)"] = min(100.0, 33 * len(projects)) if projects else MISSING_NEUTRAL_SCORE
    parts["成长路径(20)"] = 75.0 if (job.career_notes or "").strip() else MISSING_NEUTRAL_SCORE
    band = _size_to_band(org.size_band if org else "")
    parts["稳定性启发(15)"] = _SIZE_STABILITY.get(band, MISSING_NEUTRAL_SCORE)
    parts["机构偏好(10)"] = (100.0 if org and prefs.org_type_pref and org.type == prefs.org_type_pref
                             else (60.0 if not prefs.org_type_pref else 60.0))
    score = sum(parts.values()) / len(parts)
    detail = {k: f"{v:.0f}" for k, v in parts.items()}
    detail["规模档位"] = band or "未知"
    return round(min(100, score), 1), detail


_SOURCE_TYPE_TRUST = {
    "official_site": 100, "gov_public": 95, "platform_public": 70,
    "aggregator": 55, "user_import": 40, "sample": 35,
}


def compute_r(job, sources) -> tuple[float, dict]:
    """来源可信度与时效：来源可核实度 35 / 官方入口可见 25 / 时效 25 / 字段证据完整 15。"""
    parts, detail = {}, {}
    if sources:
        best = max(_SOURCE_TYPE_TRUST.get(s.source_type, 40) for s in sources)
        parts["来源可核实度(35)"] = best
        official_visible = any(
            s.access_status == "accessible" and s.source_type in ("official_site", "gov_public")
            for s in sources
        )
        platform_visible = any(s.access_status == "accessible" for s in sources)
        parts["官方入口可见(25)"] = 100.0 if official_visible else (70.0 if platform_visible else 40.0)
    else:
        parts["来源可核实度(35)"] = 30.0
        parts["官方入口可见(25)"] = 40.0

    ref = job.last_checked or job.published_at or date.today()
    days = (date.today() - ref).days if isinstance(ref, date) else 999
    freshness = 100 if days <= 7 else 85 if days <= 14 else 65 if days <= 30 else 45 if days <= 60 else 25
    parts["时效(25)"] = freshness

    core = [job.title, job.organization.canonical_name if job.organization else "",
            job.city, job.district, job.salary_max, job.published_at,
            sources[0].original_url if sources else None,
            job.locations[0].wgs84_lon if job.locations else None]
    parts["字段完整(15)"] = 100.0 * sum(1 for v in core if v) / len(core)

    score = (0.35 * parts["来源可核实度(35)"] + 0.25 * parts["官方入口可见(25)"]
             + 0.25 * parts["时效(25)"] + 0.15 * parts["字段完整(15)"])
    detail = {k: f"{v:.0f}" for k, v in parts.items()}
    detail["证据日期"] = str(ref)
    detail["距今天数"] = days
    return round(min(100, score), 1), detail


def compute_d(location, prefs) -> tuple[float, dict, bool]:
    """通勤距离：直线距离粗略分档（第一阶段不计算精确路径，明确标注）。"""
    if not prefs.commute_origin_lon or not prefs.commute_origin_lat or not location:
        return MISSING_NEUTRAL_SCORE, {"note": "未设置通勤出发点或无坐标，取中性分"}, not location
    if not location.wgs84_lon or not location.wgs84_lat or location.precision == "unlocatable":
        return MISSING_NEUTRAL_SCORE, {"note": "该职位无法定位，取中性分"}, True
    km = haversine_km(prefs.commute_origin_lon, prefs.commute_origin_lat,
                      location.wgs84_lon, location.wgs84_lat)
    if km <= 3:
        score = 95.0
    elif km <= 5:
        score = 90.0
    elif km <= 10:
        score = 80.0
    elif km <= 15:
        score = 70.0
    elif km <= 20:
        score = 60.0
    elif km <= 30:
        score = 45.0
    elif km <= 50:
        score = 30.0
    else:
        score = 15.0
    return score, {"直线距离": f"{km:.1f} km（粗略参考）"}, False


# ---- 完整度与总分 ---------------------------------------------------------

_CORE_FIELDS = [  # (字段名, 权重, 判定函数)
    ("title", 10, lambda j, o, s, l: j.title),
    ("organization", 15, lambda j, o, s, l: o.canonical_name if o else ""),
    ("city", 10, lambda j, o, s, l: j.city),
    ("district", 10, lambda j, o, s, l: j.district),
    ("salary", 15, lambda j, o, s, l: j.salary_max),
    ("published_at", 10, lambda j, o, s, l: j.published_at),
    ("source_url", 15, lambda j, o, s, l: s[0].original_url if s else None),
    ("coords", 15, lambda j, o, s, l: l[0].wgs84_lon if l else None),
]


def compute_completeness(job, org, sources, locations) -> float:
    got = sum(w for name, w, check in _CORE_FIELDS if check(job, org, sources, locations))
    total = sum(w for _, w, _ in _CORE_FIELDS)
    return round(100 * got / total, 1)


def score_job(job, prefs) -> dict:
    """对单条岗位计算全部子分与完整度（不依赖权重；权重在汇总时应用）。"""
    org = job.organization
    sources = job.sources or []
    locations = job.locations or []
    location = locations[0] if locations else None
    tokens = tokenize_keywords(prefs.keywords)

    m, m_detail = compute_m(job, org, tokens)
    p, p_detail, p_missing = compute_p(job, prefs)
    c, c_detail = compute_c(job, org, prefs, tokens)
    r, r_detail = compute_r(job, sources)
    d, d_detail, d_missing = compute_d(location, prefs)

    missing_fields = []
    if p_missing:
        missing_fields.append("salary")
    if not job.address:
        missing_fields.append("address")
    if d_missing:
        missing_fields.append("coords")
    if not job.published_at:
        missing_fields.append("published_at")

    return {
        "algorithm_version": ALGORITHM_VERSION,
        "subscores": {"M": m, "P": p, "C": c, "R": r, "D": d},
        "components": {"M": m_detail, "P": p_detail, "C": c_detail, "R": r_detail, "D": d_detail},
        "missing_fields": missing_fields,
        "completeness": compute_completeness(job, org, sources, locations),
    }


def weighted_total(subscores: dict, weights: dict) -> float:
    """综合分 = Σ w_i·s_i / Σ w_i；总权重为 0 时回退默认权重。"""
    w = weights or {}
    total_w = sum(float(w.get(k, 0) or 0) for k in ("M", "P", "C", "R", "D"))
    if total_w <= 0:
        w = DEFAULT_WEIGHTS
        total_w = sum(w.values())
    total = sum(float(w.get(k, 0) or 0) * float(subscores.get(k, MISSING_NEUTRAL_SCORE))
                for k in ("M", "P", "C", "R", "D"))
    return round(total / total_w, 1)


def status_group(status: str) -> int:
    """排序分组：可投递(在招) → 状态未核实 → 已截止/下架。"""
    return {"active": 0, "unverified": 1}.get(status, 2)
