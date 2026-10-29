"""公司信号服务：口碑情感规则引擎、法律记录分类、聚合信号与风险分 V（排序 v2 消费）。

原则（承自主方案风险 #4 与筛选设计 §9）：
- 信号是「风险提示」而非定论；match_confidence < 0.7 只展示不参与评分；
- 无证据（incomplete）取中性，不因「查不到」而加分或扣分；
- 只存帖子/案件级元数据，不存个人身份字段。
"""
from __future__ import annotations

from datetime import date

from backend.core.config import NEG_WORDS, NEGATION_PREFIXES, SIGNAL_CONF_GATE

# 法律记录类型 → 关键词（发现层摘要分类用）
LEGAL_TYPE_KEYWORDS = {
    "wage_blacklist": ["欠薪黑名单", "拖欠农民工工资", "拖欠劳动报酬", "列入失信",
                        "拒不支付劳动报酬"],
    "enforcement": ["被执行人", "失信被执行", "强制执行"],
    "labor_arbitration": ["劳动仲裁", "劳动争议", "劳动纠纷"],
    "admin_penalty": ["行政处罚", "罚款", "通报批评", "责令改正"],
    "contract_dispute": ["合同纠纷", "买卖合同", "服务合同纠纷"],
}


# ── 口碑规则引擎 ────────────────────────────────────────────

def analyze_review(text: str) -> tuple[str, list[str]]:
    """负面词命中 + 否定窗口（「不加班」不判负面）。返回 (sentiment, hit_keywords)。"""
    if not text:
        return "neutral", []
    hits: list[str] = []
    for word in NEG_WORDS:
        start = 0
        while True:
            idx = text.find(word, start)
            if idx < 0:
                break
            window = text[max(0, idx - 3):idx]
            if not any(window.endswith(neg) or neg in window[-2:] for neg in NEGATION_PREFIXES):
                hits.append(word)
                break
            start = idx + len(word)
    return ("negative" if hits else "neutral"), hits


def review_confidence(org_name: str, text: str) -> float:
    """公司名出现在标题/摘要中 → 主体匹配可信。"""
    if not org_name:
        return 0.3
    core = org_name[:6]
    if org_name in text:
        return 0.9
    if core and core in text:
        return 0.8
    return 0.4


# ── 聚合信号 ────────────────────────────────────────────────

def aggregate_review(evidences: list[dict], org_name: str = "") -> dict:
    """evidences: [{source,title,snippet,sentiment,hit_keywords,match_confidence,published_at}]"""
    usable = [e for e in evidences if (e.get("match_confidence") or 0) >= SIGNAL_CONF_GATE]
    incomplete = len(usable) == 0
    docs = len(usable)
    neg = [e for e in usable if e.get("sentiment") == "negative"]
    neg_ratio = round(len(neg) / docs, 2) if docs else 0.0
    kw: dict[str, int] = {}
    for e in neg:
        for k in e.get("hit_keywords") or []:
            kw[k] = kw.get(k, 0) + 1
    breakdown: dict[str, int] = {}
    for e in usable:
        breakdown[e.get("source") or "other"] = breakdown.get(e.get("source") or "other", 0) + 1
    dates = [e.get("published_at") for e in usable if e.get("published_at")]
    return {
        "docs": docs, "neg_docs": len(neg), "neg_ratio": neg_ratio,
        "sentiment": round(100 * (1 - neg_ratio)) if docs else 50,
        "last_date": max(dates) if dates else None,
        "hit_keywords": sorted(kw, key=kw.get, reverse=True)[:8],  # type: ignore[arg-type]
        "source_breakdown": breakdown,
        "confidence": round(min(1.0, 0.5 + 0.1 * docs), 2) if docs else 0.0,
        "incomplete": incomplete,
    }


def classify_legal(text: str) -> str:
    for rtype, words in LEGAL_TYPE_KEYWORDS.items():
        if any(w in text for w in words):
            return rtype
    return "contract_dispute"  # 兜底归为一般纠纷（展示层仍可甄别）


def aggregate_legal(records: list[dict], today: date | None = None) -> dict:
    today = today or date.today()
    usable = [r for r in records if (r.get("match_confidence") or 0) >= SIGNAL_CONF_GATE]
    counts = {"labor_arbitration": 0, "contract_dispute": 0, "enforcement": 0,
              "admin_penalty": 0, "wage_blacklist": 0}
    recent_weighted = 0.0
    last_date = None
    for r in usable:
        rtype = r.get("record_type") or "contract_dispute"
        counts[rtype] = counts.get(rtype, 0) + 1
        d = r.get("filing_date")
        if isinstance(d, str):
            try:
                from datetime import datetime
                d = datetime.strptime(d[:10], "%Y-%m-%d").date()
            except ValueError:
                d = None
        if d:
            last_date = max(last_date or d, d)
            years = (today - d).days / 365
            recent_weighted += 1.0 if years <= 2 else 0.5
    return {
        **counts, "recent_weighted": round(recent_weighted, 1),
        "last_date": str(last_date) if last_date else None,
        "confidence": round(min(1.0, 0.5 + 0.15 * len(usable)), 2) if usable else 0.0,
        "incomplete": len(usable) == 0,
    }


# ── 风险分 V（排序第六维，100 = 最安全） ───────────────────

def risk_score_V(review: dict | None, legal: dict | None) -> tuple[float, list[str]]:
    """返回 (V, flags)。门控：任一可用信号置信度不足 → 中性 50 + risk_unconfirmed。"""
    flags: list[str] = []
    review = review or {"incomplete": True, "confidence": 0.0}
    legal = legal or {"incomplete": True, "confidence": 0.0}
    usable = [s for s in (review, legal) if not s.get("incomplete")]
    if not usable:
        return 50.0, ["risk_unverified"]
    gated = [s for s in usable if (s.get("confidence") or 0) >= SIGNAL_CONF_GATE]
    if not gated:
        return 50.0, ["risk_unconfirmed"]  # 只展示不评分

    v = 100.0
    if review in gated:
        v -= min(40.0, (review.get("neg_ratio") or 0) * 100)   # 负面占比线性惩罚，封顶 40
        if review.get("docs", 0) >= 3 and (review.get("neg_ratio") or 0) >= 0.5:
            v -= 8.0                                            # 高负面样本量加罚
    if legal in gated:
        if legal.get("wage_blacklist"):
            v -= 40.0
            flags.append("wage_blacklist")
        v -= min(30.0, 10.0 * (legal.get("enforcement") or 0))
        v -= min(24.0, 8.0 * (legal.get("labor_arbitration") or 0))
        v -= min(15.0, 5.0 * (legal.get("contract_dispute") or 0))
        v -= min(18.0, 6.0 * (legal.get("admin_penalty") or 0))
    if v < 50.0:
        flags.append("risk_negative")
    return round(max(0.0, min(100.0, v)), 1), flags
