"""提取层测试：JSON-LD JobPosting / 正则兜底 / 三级层级标记。"""
from backend.pipeline.extractor import build_record, extract_by_regex, extract_jsonld

JSONLD_HTML = """
<html><head><script type="application/ld+json">
{"@type":"JobPosting","title":"后端工程师",
 "hiringOrganization":{"name":"星澜科技（成都）有限公司"},
 "jobLocation":{"address":{"addressLocality":"成都市","addressRegion":"四川","streetAddress":"天府大道1号"}},
 "baseSalary":{"value":{"minValue":15,"maxValue":25,"unitText":"MONTH"}},
 "datePosted":"2026-10-20","validThrough":"2026-11-20",
 "description":"<p>负责后端开发</p>"}
</script></head><body>...</body></html>
"""


def test_jsonld_extraction_maps_fields():
    record = extract_jsonld(JSONLD_HTML)
    assert record is not None
    assert record["title"] == "后端工程师"
    assert "星澜科技" in record["organization"]["name"]
    assert record["city"] == "成都"
    assert record["salary_min"] == 15 and record["salary_max"] == 25
    assert record["published_at"] == "2026-10-20"
    assert record["valid_through"] == "2026-11-20"
    assert record["extraction_tier"] == "jsonld"


def test_build_record_prefers_jsonld():
    record = build_record(JSONLD_HTML, "https://example.com/j/1", {"title": "ignored", "snippet": ""})
    assert record["extraction_tier"] == "jsonld"
    assert record["title"] == "后端工程师"


def test_build_record_snippet_only():
    record = build_record("", "https://example.com/j/2",
                          {"title": "成都 后端工程师 15-25K", "snippet": "星澜科技有限公司招后端 15-25K·14薪 3-5年 2026-10-21"})
    assert record["extraction_tier"] == "snippet"
    assert "后端工程师" in record["title"]
    assert record["salary_min"] == 15 and record["salary_max"] == 25
    assert record["salary_months"] == 14
    assert record["experience"] == "3-5年"
    assert record["published_at"] == "2026-10-21"
    # 公司名启发式提取（后缀词）
    assert record["organization"].get("name")


def test_regex_extraction():
    out = extract_by_regex("薪资 12-18K·13薪 经验1-3年 发布 2026/10/15")
    assert out["salary_min"] == 12 and out["salary_max"] == 18
    assert out["salary_months"] == 13
    assert out["experience"] == "1-3年"
    assert out["published_at"] == "2026-10-15"


def test_no_jsonld_returns_none():
    assert extract_jsonld("<html><body>普通页面</body></html>") is None
