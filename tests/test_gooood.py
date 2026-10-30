"""谷德垂直站 Provider 测试（T1 直取层）。"""
from backend.providers.gooood_provider import decode_jobs, parse_title


def test_parse_title_city_org_roles():
    p = parse_title("（成都）直现建筑 Epiphany Architects – 助理建筑师 / 建筑师（暂招满）")
    assert p["city"] == "成都"
    assert "直现建筑" in p["org"]
    assert "助理建筑师" in p["roles"]
    assert p["closed"] is True


def test_parse_title_variants():
    p = parse_title("（上海）木月建筑设计事务所 – 资深室内设计师（主案）")
    assert p["city"] == "上海" and "木月" in p["org"]
    p2 = parse_title("慕达建筑MUDA – Architects – 项目建筑师")
    assert "慕达建筑MUDA" in p2["org"] and "项目建筑师" in p2["roles"]
    p3 = parse_title("a9a建筑设计 a9a arch – 室内方案主创设计师 / 建筑师")
    assert "a9a" in p3["org"] and p3["closed"] is False


def test_decode_payload_structure():
    """构造迷你 Nuxt payload 验证解引用。"""
    payload = [
        {"data": 1, "prerenderedAt": -1},
        ["ShallowReactive", 2],
        {"jobs-production-index": 3},
        [4],
        {"id": 5, "title": 6, "linkurl": 7, "date": 8},
        "123",
        {"rendered": "（成都）测试所 – 设计师"},
        "https://www.gooood.cn/jobs/test",
        "2026-10-30 09:00:00",
    ]
    jobs = decode_jobs(payload)
    assert len(jobs) == 1
    assert jobs[0]["title"]["rendered"].startswith("（成都）")
    assert jobs[0]["linkurl"].endswith("/test")


def test_vertical_site_trust_registered():
    from backend.core.config import SOURCE_TYPE_TRUST, SOURCE_TYPES
    assert "vertical_site" in SOURCE_TYPES
    assert SOURCE_TYPE_TRUST["vertical_site"] == 0.75
