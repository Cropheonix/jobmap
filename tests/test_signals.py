"""公司信号服务与排序 v2（V 维度）测试。"""
from datetime import date

from backend.ranking.engine import weighted_total
from backend.signals.service import (
    aggregate_legal,
    aggregate_review,
    analyze_review,
    classify_legal,
    review_confidence,
    risk_score_V,
)


# ── 口碑规则引擎 ──────────────────────────────────────────
def test_analyze_review_negative_words():
    sentiment, kws = analyze_review("这家公司欠薪三个月，还强制加班")
    assert sentiment == "negative" and "欠薪" in kws and "强制加班" in kws


def test_analyze_review_negation_window():
    """「不加班/没有欠薪」不判负面（否定窗口豁免）。"""
    sentiment, kws = analyze_review("公司明确定调不加班、无拖欠，氛围不错")
    assert sentiment == "neutral" or all("加班" not in k for k in kws)
    s2, _ = analyze_review("从未欠薪，待遇正常")
    assert s2 == "neutral"


def test_analyze_review_empty():
    assert analyze_review("") == ("neutral", [])


def test_review_confidence_by_name_presence():
    assert review_confidence("星澜科技", "星澜科技怎么样") >= 0.9
    assert review_confidence("星澜科技", "另一家公司") <= 0.4


# ── 聚合 ────────────────────────────────────────────────
def test_aggregate_review_ratio_and_gate():
    evs = [
        {"source": "zhihu", "sentiment": "negative", "hit_keywords": ["欠薪"],
         "match_confidence": 0.9, "published_at": "2026-10-01"},
        {"source": "tieba", "sentiment": "neutral", "hit_keywords": [],
         "match_confidence": 0.9, "published_at": None},
        {"source": "xhs", "sentiment": "negative", "hit_keywords": ["裁员"],
         "match_confidence": 0.4, "published_at": None},   # 低置信度被门控
    ]
    sig = aggregate_review(evs)
    assert sig["docs"] == 2 and sig["neg_docs"] == 1 and sig["neg_ratio"] == 0.5
    assert sig["sentiment"] == 50
    assert "欠薪" in sig["hit_keywords"]


def test_aggregate_review_incomplete():
    sig = aggregate_review([])
    assert sig["incomplete"] and sig["sentiment"] == 50


def test_classify_legal_types():
    assert classify_legal("某公司被列为失信被执行人") == "enforcement"
    assert classify_legal("劳动仲裁裁决书公告") == "labor_arbitration"
    assert classify_legal("市场监管行政处罚决定书") == "admin_penalty"
    assert classify_legal("某某合同纠纷一审") == "contract_dispute"


def test_aggregate_legal_recent_weighting():
    recs = [
        {"record_type": "enforcement", "filing_date": "2026-06-01", "match_confidence": 0.9},
        {"record_type": "labor_arbitration", "filing_date": "2024-06-01", "match_confidence": 0.9},
        {"record_type": "admin_penalty", "filing_date": "2026-01-01", "match_confidence": 0.3},  # 门控
    ]
    sig = aggregate_legal(recs, today=date(2026, 10, 29))
    assert sig["enforcement"] == 1 and sig["labor_arbitration"] == 1
    assert sig["admin_penalty"] == 0
    assert abs(sig["recent_weighted"] - 1.5) < 0.01  # 近2年全额 + 2年前减半


# ── 风险分 V ────────────────────────────────────────────
def test_risk_v_neutral_when_no_evidence():
    v, flags = risk_score_V(None, None)
    assert v == 50.0 and "risk_unverified" in flags


def test_risk_v_gate_low_confidence():
    review = {"incomplete": False, "confidence": 0.5, "neg_ratio": 0.9}
    v, flags = risk_score_V(review, None)
    assert v == 50.0 and "risk_unconfirmed" in flags   # 只展示不评分


def test_risk_v_penalizes_negative_signals():
    review = {"incomplete": False, "confidence": 0.9, "neg_ratio": 0.5, "docs": 4}
    legal = {"incomplete": False, "confidence": 0.9, "enforcement": 2, "labor_arbitration": 1,
             "contract_dispute": 0, "admin_penalty": 0, "wage_blacklist": False}
    v, flags = risk_score_V(review, legal)
    assert v < 50 and "risk_negative" in flags


def test_risk_v_wage_blacklist_heavy():
    legal = {"incomplete": False, "confidence": 0.9, "wage_blacklist": True,
             "enforcement": 0, "labor_arbitration": 0, "contract_dispute": 0, "admin_penalty": 0}
    v, flags = risk_score_V(None, legal)
    assert v <= 60 and "wage_blacklist" in flags


def test_risk_v_clean_company_high():
    review = {"incomplete": False, "confidence": 0.9, "neg_ratio": 0.0, "docs": 3}
    v, _ = risk_score_V(review, None)
    assert v >= 90


# ── 六维权重 ────────────────────────────────────────────
def test_weighted_total_six_dims():
    subs = {"M": 90, "P": 75, "C": 85, "R": 80, "D": 60, "V": 40}
    w = {"M": 30, "P": 25, "C": 20, "R": 15, "D": 10, "V": 10}
    expected = round((90 * 30 + 75 * 25 + 85 * 20 + 80 * 15 + 60 * 10 + 40 * 10) / 110, 1)
    assert weighted_total(subs, w) == expected


def test_weighted_total_v_zero_disables_risk():
    """V 权重调零 = 整体关闭风险维度（公平性开关）。"""
    subs = {"M": 90, "P": 75, "C": 85, "R": 80, "D": 60, "V": 0}
    with_v = weighted_total(subs, {"M": 30, "P": 25, "C": 20, "R": 15, "D": 10, "V": 10})
    without_v = weighted_total(subs, {"M": 30, "P": 25, "C": 20, "R": 15, "D": 10, "V": 0})
    assert without_v > with_v
