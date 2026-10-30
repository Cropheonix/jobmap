"""轻量后台调度器：OrgWatch 官网监测定时自动运行（不引入 APScheduler 依赖）。

策略：daemon 线程每 6 小时醒一次，距上次成功运行 ≥ watch_interval_days 天时
自动执行 run_watch()。状态存 data/watch-state.json，全部行为入 audit_events。
"""
from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

from backend.core.config import BASE_DIR

_STATE_PATH = BASE_DIR / "data" / "watch-state.json"
_CHECK_INTERVAL = 6 * 3600  # 每 6 小时检查一次

_started = False


def _load_state() -> dict:
    if not _STATE_PATH.exists():
        return {"last_run": None, "last_summary": None}
    try:
        return json.loads(_STATE_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"last_run": None, "last_summary": None}


def _save_state(state: dict) -> None:
    _STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    _STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")


def _watch_due(interval_days: int) -> bool:
    state = _load_state()
    if not state.get("last_run"):
        return False  # 从未手动跑过：不自动首跑（等用户第一次手动运行建立目录）
    last = datetime.fromisoformat(state["last_run"])
    return datetime.now() - last >= timedelta(days=interval_days)


def _run_once(reason: str) -> dict:
    from backend.providers.watch import run_watch
    summary = run_watch(limit=100)
    _save_state({"last_run": datetime.now().isoformat(timespec="seconds"),
                 "last_summary": {"orgs": summary.get("orgs"), "added": summary.get("added"),
                                  "removed": summary.get("removed"), "reason": reason}})
    from backend.db.database import db_scope
    from backend.db.models import AuditEvent
    with db_scope() as db:
        db.add(AuditEvent(event_type="watch_auto", detail={
            "reason": reason, "orgs": summary.get("orgs"),
            "added": summary.get("added"), "removed": summary.get("removed")}))
    return summary


def _loop() -> None:  # pragma: no cover（常驻线程）
    while True:
        time.sleep(_CHECK_INTERVAL)
        try:
            from backend.db.database import db_scope
            from backend.db.models import UserPreference
            with db_scope() as db:
                prefs = db.get(UserPreference, 1)
                enabled = bool(prefs.watch_auto_enabled) if prefs and prefs.watch_auto_enabled is not None else True
                interval = prefs.watch_interval_days if prefs and prefs.watch_interval_days else 7
            if enabled and _watch_due(int(interval)):
                _run_once(f"schedule:{interval}d")
        except Exception:
            pass  # 调度失败不影响主服务


def start_scheduler() -> None:
    global _started
    if _started:
        return
    _started = True
    threading.Thread(target=_loop, daemon=True, name="jobmap-watch-scheduler").start()


def mark_manual_run() -> dict:
    """手动「官网监测」按钮调用后记录状态（自动调度以此为基准做增量）。"""
    return _save_state({"last_run": datetime.now().isoformat(timespec="seconds"),
                        "last_summary": {"reason": "manual"}})


def watch_status() -> dict:
    return _load_state()
