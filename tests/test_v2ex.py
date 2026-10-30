"""V2EX 酷工作 Provider 测试（T1 直取，官方公开 API）。"""
from backend.providers.v2ex_provider import parse_topic


def _topic(**kw):
    base = dict(id=999, title="[成都] 某科技公司 招后端工程师 15-25K",
                content="负责后端开发。Python/Go。待遇 15-25K·13薪，3-5年经验。",
                url="https://www.v2ex.com/t/999", created=1770000000)
    base.update(kw)
    return base


def test_parse_topic_city_and_salary():
    r = parse_topic(_topic())
    assert r["city"] == "成都"
    assert "某科技公司" in r["organization"]["name"]
    assert r["salary_min"] == 15 and r["salary_max"] == 25
    assert r["salary_months"] == 13
    assert r["published_at"]  # unix 时间戳转日期
    assert r["sources"][0]["source_type"] == "vertical_site"


def test_parse_topic_no_city():
    r = parse_topic(_topic(title="招聘 Rust 工程师（量化交易系统）", content="远程，面议。"))
    assert r["city"] == ""
    assert r["title"]


def test_parse_topic_html_stripped():
    r = parse_topic(_topic(content="<p>待遇<b>20-30K</b></p>"))
    assert "<" not in r["description_summary"] and "20-30K" in r["description_summary"]
