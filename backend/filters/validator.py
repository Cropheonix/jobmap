"""S1 结构与字段校验：字段级错误只销毁字段，记录级错误才销毁记录。"""
from __future__ import annotations

from datetime import date


def check_required(record: dict) -> str | None:
    """返回记录级错误原因（None 表示通过）。"""
    if not (record.get("title") or "").strip():
        return "missing_required: 职位名为空"
    if not ((record.get("organization") or {}).get("name") or "").strip():
        return "missing_required: 公司名为空"
    if not (record.get("sources") or []):
        return "missing_required: 无来源 URL"
    return None


def validate_fields(record: dict, today: date | None = None) -> list[str]:
    """返回字段级问题标记（记录保留，字段作废由调用方执行）。"""
    today = today or date.today()
    flags: list[str] = []

    smin, smax = record.get("salary_min"), record.get("salary_max")
    if smin is not None or smax is not None:
        if (smin is not None and (smin < 0 or smin > 500)) or \
           (smax is not None and (smax < 0 or smax > 500)) or \
           (smin is not None and smax is not None and smin > smax):
            flags.append("salary_invalid")
            record["salary_min"] = record["salary_max"] = record["salary_months"] = None

    pub = record.get("published_at")
    if isinstance(pub, str) and pub:
        try:
            from datetime import datetime
            d = datetime.strptime(pub[:10], "%Y-%m-%d").date()
            if d > today:
                flags.append("date_invalid")
                record["published_at"] = None
        except ValueError:
            flags.append("date_invalid")
            record["published_at"] = None

    city = (record.get("city") or "").strip()
    if city and city not in _KNOWN_CITIES:
        flags.append("geo_unverified")
    return flags


# 常见城市白名单（过滤明显非城市的脏值；不全的走 geo_unverified 而非拒绝）
_KNOWN_CITIES = {
    "成都", "北京", "上海", "深圳", "广州", "杭州", "重庆", "武汉", "西安", "南京",
    "苏州", "长沙", "郑州", "天津", "合肥", "福州", "厦门", "青岛", "济南", "大连",
    "宁波", "无锡", "佛山", "东莞", "沈阳", "哈尔滨", "石家庄", "太原", "南昌", "贵阳",
    "昆明", "南宁", "兰州", "乌鲁木齐", "珠海", "中山", "惠州", "常州", "温州", "泉州",
}
