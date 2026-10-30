"""搜索任务接口：提交、进度、取消。"""
import threading

from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.concurrency import run_in_threadpool  # noqa: F401  (保留给 M2 异步源)
from sqlalchemy import select

from backend.api.system import get_prefs
from backend.core.security import require_token
from backend.db.database import db_scope, get_db
from backend.db.models import AuditEvent, SearchTask
from backend.providers.registry import REGISTRY

router = APIRouter()

# 任务取消标记（进程内）
_cancel_flags: dict[int, bool] = {}

_STEP_ORDER = ["discover", "extract", "dedup", "rank", "geo", "enrich", "export"]
_STEP_LABELS = {
    "discover": "多源搜索", "extract": "信息提取", "dedup": "去重校验",
    "rank": "权重排序", "geo": "地图标点", "enrich": "公司核查", "export": "导出报告",
}


def _task_dto(task: SearchTask) -> dict:
    steps = task.steps or []
    by_name = {s.get("step"): s for s in steps}
    # 公司核查步骤仅对联网搜索任务展示（示例数据任务无核查语义）
    order = list(_STEP_ORDER)
    if "searxng_discovery" not in (task.providers or []):
        order = [s for s in order if s != "enrich"]
    ordered = []
    for name in order:
        s = by_name.get(name) or {"step": name, "state": "pending", "counts": {}}
        s = dict(s)
        s["label"] = _STEP_LABELS[name]
        ordered.append(s)
    return {
        "id": task.id,
        "state": task.state,
        "query": task.query_json,
        "providers": task.providers,
        "steps": ordered,
        "counts": task.counts,
        "errors": task.errors,
        "funnel": task.funnel or {},
        "started_at": task.started_at.isoformat() if task.started_at else None,
        "ended_at": task.ended_at.isoformat() if task.ended_at else None,
    }


def _run_task(task_id: int, provider_names: list[str]) -> None:
    from backend.db.seed import recompute_all_scores

    with db_scope() as db:
        task = db.get(SearchTask, task_id)
        if not task:
            return
        task.state = "running"
        db.commit()

    query = task.query_json if task else {}
    steps_state: list[dict] = []
    errors: list[dict] = []
    counts: dict = {}

    def on_step(update: dict):
        nonlocal steps_state
        merged = {s["step"]: s for s in steps_state}
        merged[update["step"]] = update
        steps_state = [merged[k] for k in sorted(merged, key=lambda n: _STEP_ORDER.index(n) if n in _STEP_ORDER else 99)]
        with db_scope() as db:
            t = db.get(SearchTask, task_id)
            if t:
                t.steps = steps_state
                db.commit()

    def cancelled() -> bool:
        return _cancel_flags.get(task_id, False)

    try:
        for name in provider_names:
            provider = REGISTRY.get(name)
            if not provider or provider.capability.status != "available":
                errors.append({"provider": name, "error": "数据源不可用或未接入（M2）"})
                continue
            if cancelled():
                break
            try:
                result = provider.discover(query, on_step=on_step, cancelled=cancelled)
                counts.update({name: result})
            except Exception as exc:
                errors.append({"provider": name, "error": str(exc)})
        if provider_names:
            recompute_all_scores()
        state = "cancelled" if cancelled() else "done"
        # 漏斗与覆盖率进任务记录（供 UI 与报告复现）
        for name, result in counts.items():
            if isinstance(result, dict) and result.get("funnel"):
                with db_scope() as db:
                    t = db.get(SearchTask, task_id)
                    if t:
                        t.funnel = result["funnel"]
                        db.commit()
                break
    except Exception as exc:
        errors.append({"provider": "pipeline", "error": str(exc)})
        state = "failed"

    from datetime import datetime, timezone

    with db_scope() as db:
        t = db.get(SearchTask, task_id)
        if t:
            t.state = state
            t.counts = counts
            t.errors = errors
            t.ended_at = datetime.now(timezone.utc)
            db.commit()
        db.add(AuditEvent(event_type="search_task", record_id=str(task_id),
                          detail={"state": state, "counts": counts, "errors": errors}))
    _cancel_flags.pop(task_id, None)


# 智能选源：意图类别 → 垂直源子集（docs/search-scheme 四层架构；减少无效请求与任务时长）
_VERTICAL_BY_CATEGORY = {
    "设计创意": ["gooood"],
    "互联网/软件": ["v2ex", "remote_work"],
    "金融/财务": ["jrzp", "iguopin"],
    "教育/科研": ["jrzp", "iguopin"],
    "医疗健康": ["jrzp", "iguopin"],
    "建筑/地产": ["gooood", "iguopin"],
    "销售/市场": ["iguopin", "remote_work"],
    "职能/法律/人力": ["jrzp", "iguopin"],
    "供应链/物流": ["iguopin", "remote_work"],
    "制造/工业": ["iguopin"],
    "其他行业": ["iguopin", "remote_work", "jrzp"],
}
_VERTICAL_ALL = ["iguopin", "jrzp", "gooood", "v2ex", "remote_work"]


def pick_providers(keywords: list[str], city: str) -> list[str]:
    """按意图类别挑选垂直源；查询无法归类时用全源（宁多勿漏）。"""
    from backend.core.config import SEARXNG_EMBEDDED_URL
    from backend.db.database import db_scope as _scope
    from backend.db.models import UserPreference
    from backend.discovery.searxng import SearxngBackend
    from backend.filters import intent as intent_mod

    it = intent_mod.parse_intent(" ".join([*keywords, city]))
    verticals = list(_VERTICAL_BY_CATEGORY.get(it.category, _VERTICAL_ALL))
    # 联网搜索通道在线时追加（T2 索引层；离线/限流自动省略）
    try:
        with _scope() as db:
            prefs = db.get(UserPreference, 1)
            url = (prefs.external_searxng_url or "").strip() if prefs else ""
        if SearxngBackend(url or SEARXNG_EMBEDDED_URL).health():
            verticals.append("searxng_discovery")
    except Exception:
        pass
    verticals.append("sample")  # 演示数据源兜底
    return verticals


@router.post("/search/tasks")
def create_task(
    body: dict,
    db=Depends(get_db),
    token: str | None = Header(default=None, alias="X-JobMap-Token"),
):
    if not require_token(token):
        raise HTTPException(403, "缺少或错误的本地会话令牌")
    keywords = body.get("keywords") or []
    if isinstance(keywords, str):
        keywords = [k for k in keywords.replace("，", " ").replace(",", " ").split() if k]
    query = {"keywords": keywords, "city": (body.get("city") or "").strip(),
             "category": body.get("category") or ""}
    # 智能选源：providers 为 ["auto"]/缺省时按意图类别挑选；显式指定则尊重用户
    raw_providers = body.get("providers") or ["auto"]
    if raw_providers == ["auto"] or not raw_providers:
        provider_names = pick_providers(keywords, query["city"])
    else:
        provider_names = raw_providers
    # 可执行源：示例/导入/联网搜索（联网搜索不可用时在 discover 内给出明确错误说明）
    runnable = [n for n in provider_names
                if n in REGISTRY and (REGISTRY[n].capability.status == "available"
                                      or n == "searxng_discovery")]
    if not runnable:
        raise HTTPException(422, "没有可执行的数据源（其余来源将在 M2 接入）")

    prefs = get_prefs(db)
    if keywords:
        prefs.keywords = keywords
    if query["city"]:
        prefs.city = query["city"]
    db.commit()

    task = SearchTask(query_json=query, providers=runnable, state="pending")
    db.add(task)
    db.commit()
    threading.Thread(target=_run_task, args=(task.id, runnable), daemon=True).start()
    return {"id": task.id, "state": task.state, "providers": runnable}


@router.get("/search/tasks")
def list_tasks(limit: int = 20, db=Depends(get_db)):
    rows = db.execute(
        select(SearchTask).order_by(SearchTask.started_at.desc()).limit(min(limit, 50))
    ).scalars().all()
    return {"tasks": [_task_dto(t) for t in rows]}


@router.get("/search/tasks/{task_id}")
def get_task(task_id: int, db=Depends(get_db)):
    task = db.get(SearchTask, task_id)
    if not task:
        raise HTTPException(404, "任务不存在")
    return _task_dto(task)


@router.post("/search/tasks/{task_id}/cancel")
def cancel_task(
    task_id: int,
    db=Depends(get_db),
    token: str | None = Header(default=None, alias="X-JobMap-Token"),
):
    if not require_token(token):
        raise HTTPException(403, "缺少或错误的本地会话令牌")
    task = db.get(SearchTask, task_id)
    if not task:
        raise HTTPException(404, "任务不存在")
    if task.state in ("running", "pending"):
        _cancel_flags[task_id] = True
        return {"id": task_id, "state": "cancelling"}
    return {"id": task_id, "state": task.state}
