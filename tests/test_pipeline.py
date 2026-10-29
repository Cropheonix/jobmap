"""坐标换算与去重管线测试。"""
from backend.pipeline.coords import gcj02_to_wgs84, haversine_km, wgs84_to_gcj02
from backend.pipeline.dedup import normalize_company, normalize_url, title_similarity


def test_gcj02_conversion_roundtrip():
    lon, lat = 104.0665, 30.5723  # 成都天府广场附近
    glon, glat = wgs84_to_gcj02(lon, lat)
    # 国测局偏移量级：约几百米
    assert abs(glon - lon) < 0.01 and abs(glat - lat) < 0.01
    assert glon != lon
    back_lon, back_lat = gcj02_to_wgs84(glon, glat)
    assert abs(back_lon - lon) < 0.0001 and abs(back_lat - lat) < 0.0001  # 回代误差 <10m


def test_out_of_china_unchanged():
    lon, lat = -0.1276, 51.5072  # 伦敦
    assert wgs84_to_gcj02(lon, lat) == (lon, lat)


def test_haversine_chengdu_distance():
    # 天府广场到高新区的直线距离约 12km
    d = haversine_km(104.0665, 30.5723, 104.0727, 30.5723 + 0.1)
    assert 8 < d < 15


def test_normalize_url_strips_tracking():
    url = "https://Example.com/careers/123/?utm_source=x&utm_campaign=y&id=9"
    assert normalize_url(url) == "https://example.com/careers/123?id=9"


def test_normalize_url_strips_trailing_slash():
    assert normalize_url("https://a.com/x/") == "https://a.com/x"


def test_normalize_company_strips_suffix_and_region():
    assert normalize_company("方物空间设计（成都）有限责任公司") == "方物空间设计"
    assert normalize_company("ABC 集团") == "abc"


def test_title_similarity_high_for_same():
    # 同名岗位 + 括号细分后缀：相似但低于 0.82 自动合并阈值（方案 4.4 保守合并，由用户确认）
    sim = title_similarity("商业空间设计师", "商业空间设计师（零售方向）")
    assert 0.6 <= sim < 0.82
    assert title_similarity("商业空间设计师", "商业空间设计师") == 1.0
    assert title_similarity("后端工程师", "幼儿园教师") < 0.3
