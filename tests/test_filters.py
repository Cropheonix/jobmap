"""筛选核心算法测试（对应 docs/filter-algorithm-design.md 验收标准）。"""
from datetime import date, timedelta

from backend.filters.confidence import compute_c, consistency, field_completeness
from backend.filters.entity import entity_confidence, is_ambiguous
from backend.filters.funnel import FunnelTracker
from backend.filters.intent import parse_intent
from backend.filters.freshness import status_gate
from backend.filters.pipeline import run_filters
from backend.filters.relevance import relevance
from backend.filters.validator import check_required, validate_fields


def _record(**kw):
    base = dict(
        organization={"name": "星澜科技"}, title="Python 后端工程师",
        description_summary="负责后端服务开发，Python/FastAPI",
        city="成都", district="高新区", address="天府大道",
        salary_min=15, salary_max=25, salary_months=14,
        experience="3-5年", education="本科", job_category="互联网/软件",
        keywords=["Python"], published_at="2026-10-20", last_checked="2026-10-28",
        sources=[{"provider": "搜索发现", "url": "https://example.com/j/1",
                  "source_type": "platform_public", "access_status": "accessible"}],
        extraction_tier="jsonld", location={"lon": 104.0, "lat": 30.5},
        valid_through=None, status_hint=None,
    )
    base.update(kw)
    return base


# ── S0 意图 ──────────────────────────────────────────────
def test_intent_parse_basic():
    it = parse_intent("成都 后端工程师 -外包")
    assert it.city == "成都"
    assert "后端" in it.role_terms           # 整词命中后端词组
    assert it.primary_role == "后端"
    assert it.exclude_terms == ["外包"]
    assert it.category == "互联网/软件"


def test_intent_city_alias():
    assert parse_intent("蓉城 前端").city == "成都"


# ── S1 校验 ──────────────────────────────────────────────
def test_required_missing():
    assert check_required(_record(title="")) .startswith("missing_required")
    assert check_required(_record(sources=[])) is not None
    assert check_required(_record()) is None


def test_field_level_salary_invalid():
    r = _record(salary_min=30, salary_max=20)
    flags = validate_fields(r, today=date(2026, 10, 29))
    assert "salary_invalid" in flags
    assert r["salary_min"] is None  # 字段作废，记录保留


def test_field_level_future_date():
    r = _record(published_at="2027-01-01")
    flags = validate_fields(r, today=date(2026, 10, 29))
    assert "date_invalid" in flags and r["published_at"] is None


# ── S2 相关性 ────────────────────────────────────────────
def test_relevance_high_for_match():
    it = parse_intent("成都 后端")
    score, _ = relevance(it, _record())
    assert score >= 0.7


def test_relevance_city_mismatch():
    it = parse_intent("成都 后端")
    score_match, _ = relevance(it, _record(city="成都"))
    score_mismatch, _ = relevance(it, _record(city="北京"))
    assert score_mismatch < score_match
    # 城市不符在流水线中强制降为疑似区（city_mismatch 标记）
    result = run_filters(_record(city="北京"), it, today=date(2026, 10, 29))
    assert result.zone == "accepted_suspect" and "city_mismatch" in result.flags


# ── S3 实体 ──────────────────────────────────────────────
def test_entity_uscc_match():
    conf, _ = entity_confidence(record_name="星澜科技", record_uscc="91510100X",
                                record_region="成都", org_name="星澜科技有限公司",
                                org_uscc="91510100X", org_region="成都")
    assert conf == 1.0


def test_entity_name_only_lower_conf():
    conf, _ = entity_confidence(record_name="星澜科技", record_uscc=None,
                                record_region="", org_name="星澜科技", org_uscc=None,
                                org_region=None)
    assert conf == 0.75


def test_entity_ambiguity_flag():
    assert is_ambiguous("星澜科技", 2)
    assert not is_ambiguous("星澜科技", 1)


# ── S4 状态门控 ──────────────────────────────────────────
def test_status_valid_through_expired():
    status, reason, _ = status_gate(_record(valid_through="2026-10-01"), today=date(2026, 10, 29))
    assert status == "expired" and "validThrough" in reason


def test_status_snippet_only_unverified():
    status, _, flags = status_gate(_record(extraction_tier="snippet"), today=date(2026, 10, 29))
    assert status == "unverified" and "snippet_only" in flags


def test_status_active_within_window():
    status, _, flags = status_gate(_record(), today=date(2026, 10, 29))
    assert status == "active" and not flags


def test_status_stale():
    status, _, flags = status_gate(_record(published_at="2026-05-01", last_checked="2026-08-01"),
                                   today=date(2026, 10, 29))
    assert status == "unverified" and "stale" in flags


# ── S5 置信度 ────────────────────────────────────────────
def test_consistency_conflict():
    c, flags = consistency([{"salary_min": 15}], {"salary_min": 40})
    assert c == 0.2 and any("conflict" in f for f in flags)


def test_consistency_agree():
    c, flags = consistency([{"city": "成都"}], {"city": "成都"})
    assert c == 1.0 and not flags


def test_compute_c_bounds():
    assert compute_c(source_type="official_site", extraction_tier="jsonld",
                     field_completeness=100, entity_conf=1.0, consistency_score=1.0) == 1.0
    assert compute_c(source_type="aggregator", extraction_tier="snippet",
                     field_completeness=50, entity_conf=0.5, consistency_score=0.5) < 0.7


# ── 流水线端到端 ─────────────────────────────────────────
def test_pipeline_accepted():
    it = parse_intent("成都 后端")
    result = run_filters(_record(), it, today=date(2026, 10, 29))
    assert result.zone == "accepted"
    assert result.status == "active"
    assert result.confidence > 0.7


def test_pipeline_quarantine_low_relevance():
    it = parse_intent("成都 护士")
    result = run_filters(_record(), it, today=date(2026, 10, 29))
    assert result.zone == "quarantined" and result.rejection_reason == "low_relevance"


def test_pipeline_user_excluded():
    it = parse_intent("成都 后端 -外包")
    result = run_filters(_record(title="外包 后端工程师"), it, today=date(2026, 10, 29))
    assert result.zone == "quarantined" and result.rejection_reason == "user_excluded"


def test_pipeline_suspect_zone():
    it = parse_intent("成都 后端")
    r = _record(title="全栈工程师（含后端）", city="重庆")
    result = run_filters(r, it, today=date(2026, 10, 29))
    assert result.zone in ("accepted_suspect", "quarantined")  # 灰区保留或隔离，不静默
    if result.zone == "accepted_suspect":
        assert any(s["stage"] == "S2" for s in result.stages)


def test_pipeline_trace_reproducible():
    it = parse_intent("成都 后端")
    r1 = run_filters(_record(), it, today=date(2026, 10, 29))
    r2 = run_filters(_record(), it, today=date(2026, 10, 29))
    assert r1.to_trace() == r2.to_trace()  # 断网重放判定不变


# ── 漏斗 ────────────────────────────────────────────────
def test_funnel_saturation():
    f = FunnelTracker()
    assert not f.saturated(0.05)
    f.add_batch(["a", "b", "c", "d"])
    f.add_batch(["a", "b", "e"])  # 新增 1/3 ≈ 0.33，不饱和
    assert not f.saturated(0.05)
    f.add_batch(["a", "b"])       # 新增 0
    f.add_batch(["a"])            # 新增 0
    assert f.saturated(0.05)


def test_funnel_coverage_estimate():
    f = FunnelTracker()
    f.register_engine_results("bing", {f"u{i}" for i in range(20)})
    shared = {f"u{i}" for i in range(10)}
    f.register_engine_results("baidu", shared | {f"v{i}" for i in range(10)})
    cov = f.coverage_estimate()  # |A|=20 |B|=20 交集10 → N̂=40，观测30 → 0.75
    assert cov == 0.75
