"""内置示例数据源：从 sample-data 加载演示数据，模拟多源搜索流程。

演示数据均为虚构（is_demo=true），来源 URL 指向 example.com。
"""
import json
import time

from backend.core.config import SAMPLE_DATA_PATH
from backend.db.database import db_scope
from backend.db.models import Job, SearchTask
from backend.pipeline.loader import upsert_job_record
from backend.providers.base import ProviderCapability, SearchProvider
from backend.ranking.engine import tokenize_keywords
from sqlalchemy import select


class SampleProvider(SearchProvider):
    capability = ProviderCapability(
        name="sample", display_name="内置示例数据", source_type="sample",
        status="available", rate_limit="无（本地数据）",
        license_notes="虚构演示数据，非真实招聘信息，仅用于功能演示。",
    )

    def discover(self, query: dict, on_step=None, cancelled=None) -> dict:
        records = self._load_records()
        tokens = tokenize_keywords(query.get("keywords") or [])
        city = (query.get("city") or "").strip()

        matched = []
        for r in records:
            if city and (r.get("city") or "") != city:
                continue
            if tokens:
                blob = " ".join([
                    r.get("title", ""), r.get("description_summary", ""),
                    " ".join(r.get("keywords") or []), r.get("industry", ""),
                    (r.get("organization") or {}).get("name", ""),
                ]).lower()
                if not any(t in blob for t in tokens):
                    continue
            matched.append(r)
        if not matched:
            matched = records  # 关键词无命中时返回全部，由筛选器负责过滤

        counts = {"discovered": 0, "extracted": 0, "merged": 0, "created": 0,
                  "geo_located": 0, "errors": 0}

        def report(step, state="running", extra=None):
            if on_step:
                on_step({"step": step, "state": state, "counts": dict(counts), **(extra or {})})

        report("discover")
        time.sleep(0.4)
        counts["discovered"] = len(matched)
        report("discover", "done")

        report("extract")
        time.sleep(0.5)
        counts["extracted"] = len(matched)
        report("extract", "done")

        report("dedup")
        time.sleep(0.3)
        for i, record in enumerate(matched):
            if cancelled and cancelled():
                report("dedup", "cancelled")
                counts["errors"] = counts.get("errors", 0)
                return {**counts, "cancelled": True}
            try:
                with db_scope() as db:
                    result, job, reason = upsert_job_record(db, record)
                counts["merged" if result == "merged" else "created"] += 1
                if (record.get("location") or {}).get("lon") is not None:
                    counts["geo_located"] += 1
            except Exception as exc:  # 单条失败不阻断
                counts["errors"] += 1
                counts.setdefault("error_details", []).append(f"{record.get('title')}: {exc}")
            if i % 10 == 0:
                report("dedup")
        report("dedup", "done")

        report("rank")
        time.sleep(0.3)
        report("rank", "done")

        report("geo")
        time.sleep(0.2)
        report("geo", "done")

        report("export")
        time.sleep(0.2)
        report("export", "done")
        return counts

    def check_policy(self, url: str) -> bool:
        return "example.com" in url

    def health_check(self) -> dict:
        return {"name": self.capability.display_name, "status": "available",
                "ok": SAMPLE_DATA_PATH.exists(), "records": len(self._load_records())}

    @staticmethod
    def _load_records() -> list[dict]:
        if not SAMPLE_DATA_PATH.exists():
            return []
        with open(SAMPLE_DATA_PATH, encoding="utf-8") as f:
            return json.load(f)
