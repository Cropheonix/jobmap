"""评分引擎边界测试（对应方案 5.3 演算与 10. 质量标准）。"""
from types import SimpleNamespace

from backend.core.config import DEFAULT_WEIGHTS
from backend.ranking.engine import (
    compute_p,
    score_job,
    status_group,
    tokenize_keywords,
    weighted_total,
)


def _job(**kw):
    base = dict(
        title="商业空间设计师", description_summary="负责零售空间设计",
        city="成都", district="武侯区", address="某街道",
        salary_min=10, salary_max=18, salary_months=13,
        experience="3-5年", education="本科", job_category="设计创意",
        industry="空间设计", keywords=["CAD"], published_at="2026-10-20",
        status="active", status_reason="", career_notes="", last_checked="2026-10-28",
        is_demo=True,
        organization=SimpleNamespace(
            canonical_name="方物空间设计", type="studio", industry="空间设计",
            size_band="15-50人", verified_projects=["A 项目"]),
        sources=[SimpleNamespace(source_type="official_site", access_status="accessible",
                                 original_url="https://example.com/1")],
        locations=[SimpleNamespace(wgs84_lon=104.05, wgs84_lat=30.62, precision="block")],
    )
    base.update(kw)
    return SimpleNamespace(**base)


def _prefs(**kw):
    base = dict(keywords=["商业空间"], salary_expect_min=12, salary_expect_max=20,
                commute_origin_lon=104.06, commute_origin_lat=30.57, org_type_pref="studio")
    base.update(kw)
    return SimpleNamespace(**base)


def test_plan_example_calculation():
    """方案 5.3 的 v1.0 演算样例（五维显式权重；缺省 V 取中性 50）。"""
    subs = {"M": 90, "P": 75, "C": 85, "R": 80, "D": 60}
    weights = {"M": 30, "P": 25, "C": 20, "R": 15, "D": 10}  # v1.0 五维口径
    total = weighted_total(subs, weights)
    expected = round((90 * 30 + 75 * 25 + 85 * 20 + 80 * 15 + 60 * 10) / 100, 1)
    assert total == expected == 80.8


def test_zero_weights_fall_back_to_default():
    subs = {"M": 90, "P": 75, "C": 85, "R": 80, "D": 60, "V": 50}
    total = weighted_total(subs, {"M": 0, "P": 0, "C": 0, "R": 0, "D": 0, "V": 0})
    assert total == weighted_total(subs, {})  # 零权重回退默认权重，结果一致


def test_weight_change_reorders():
    subs_a = {"M": 90, "P": 50, "C": 50, "R": 50, "D": 50}
    subs_b = {"M": 50, "P": 90, "C": 50, "R": 50, "D": 50}
    w_m = {"M": 80, "P": 5, "C": 5, "R": 5, "D": 5}
    w_p = {"M": 5, "P": 80, "C": 5, "R": 5, "D": 5}
    assert weighted_total(subs_a, w_m) > weighted_total(subs_b, w_m)
    assert weighted_total(subs_a, w_p) < weighted_total(subs_b, w_p)


def test_missing_salary_is_neutral_not_zero():
    job = _job(salary_min=None, salary_max=None, salary_months=None)
    score, detail, missing = compute_p(job, _prefs())
    assert score == 50
    assert missing is True


def test_salary_no_expectation_neutral():
    score, _, missing = compute_p(_job(), _prefs(salary_expect_min=None, salary_expect_max=None))
    assert score == 50 and missing is False


def test_salary_overlap_scores_high():
    score, _, _ = compute_p(_job(salary_min=12, salary_max=20), _prefs())
    assert score >= 95  # 完全包含在期望区间内


def test_score_job_collects_missing_and_completeness():
    job = _job(salary_min=None, salary_max=None, address="", locations=[])
    result = score_job(job, _prefs())
    assert "salary" in result["missing_fields"]
    assert "address" in result["missing_fields"]
    assert "coords" in result["missing_fields"]
    assert 0 <= result["completeness"] <= 100
    for key in ("M", "P", "C", "R", "D"):
        assert 0 <= result["subscores"][key] <= 100


def test_no_keywords_gives_neutral_match():
    result = score_job(_job(), _prefs(keywords=[]))
    assert result["subscores"]["M"] == 50


def test_status_group_ordering():
    assert status_group("active") < status_group("unverified") < status_group("expired")


def test_tokenize_keywords_dedupe_and_case():
    assert tokenize_keywords(["Python", "python", "后端 设计"]) == ["python", "后端", "设计"]
