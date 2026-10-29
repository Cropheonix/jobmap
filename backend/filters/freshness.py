"""S4 时效与状态门控：page_accessible ≠ actively_hiring。"""
from __future__ import annotations

from datetime import date, datetime

CLOSED_MARKERS = ["已截止", "已招满", "停止招聘", "停止招人", "招聘结束", "已下线", "已关闭"]


def _parse(value) -> date | None:
    if value in (None, ""):
        return None
    if isinstance(value, date):
        return value
    try:
        return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def status_gate(record: dict, today: date | None = None) -> tuple[str, str, list[str]]:
    """返回 (status, reason, flags)。优先级：明示截止 > 陈旧 > 仅摘要 > 正常。"""
    today = today or date.today()
    flags: list[str] = []

    text = " ".join([record.get("title") or "", record.get("status_hint") or ""])
    if any(m in text for m in CLOSED_MARKERS):
        return "expired", "页面明示已截止/招满", flags

    valid_through = _parse(record.get("valid_through"))
    if valid_through and valid_through < today:
        return "expired", "validThrough 已过期", flags

    published = _parse(record.get("published_at"))
    checked = _parse(record.get("last_checked"))
    if record.get("extraction_tier") == "snippet":
        flags.append("snippet_only")
        return "unverified", "仅索引摘要可见，原文未核查", flags

    if published and (today - published).days > 90:
        if (not checked or (today - checked).days > 30):
            flags.append("stale")
            return "unverified", "发布超过 90 天且超过 30 天未复核", flags

    return "active", "发布 90 天窗口内且无负面时效证据", flags
