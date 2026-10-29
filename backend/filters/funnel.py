"""漏斗追踪：六阶段通过率、饱和度早停、Lincoln-Petersen 覆盖率估计。"""
from __future__ import annotations

STAGE_ORDER = ["S0", "S1", "S2", "S3", "S4", "S5"]


class FunnelTracker:
    def __init__(self):
        self.counts: dict[str, dict[str, int]] = {s: {"pass": 0, "fail": 0} for s in STAGE_ORDER}
        self._batch_unique_ratio: list[float] = []
        self._seen_urls: set[str] = set()
        self.engine_sets: dict[str, set[str]] = {}

    def record_stage(self, stage: str, passed: bool) -> None:
        if stage in self.counts:
            self.counts[stage]["pass" if passed else "fail"] += 1

    def add_batch(self, urls: list[str]) -> float:
        """登记一个查询批次的结果 URL，返回本批唯一新增率。"""
        if not urls:
            return 0.0
        unique_new = [u for u in urls if u not in self._seen_urls]
        self._seen_urls.update(urls)
        ratio = len(unique_new) / len(urls)
        self._batch_unique_ratio.append(ratio)
        return ratio

    def register_engine_results(self, engine: str, urls: list[str]) -> None:
        self.engine_sets.setdefault(engine, set()).update(urls)

    def saturated(self, threshold: float = 0.05) -> bool:
        """连续 2 批唯一新增率低于阈值 → 饱和，可早停低优先级查询。"""
        if len(self._batch_unique_ratio) < 2:
            return False
        return all(r < threshold for r in self._batch_unique_ratio[-2:])

    def coverage_estimate(self) -> float | None:
        """Lincoln-Petersen：N̂≈|A||B|/|A∩B|，返回估计覆盖率（观测/估计，截顶 1.0）。"""
        engines = [s for s in self.engine_sets.values() if len(s) >= 3]
        if len(engines) < 2:
            return None
        a, b = engines[0], engines[1]
        inter = a & b
        if not inter:
            return None
        estimate = (len(a) * len(b)) / len(inter)
        if estimate <= 0:
            return None
        observed = len(a | b)
        return round(min(1.0, observed / estimate), 2)

    def pass_rates(self) -> dict:
        out = {}
        for stage in STAGE_ORDER:
            c = self.counts[stage]
            total = c["pass"] + c["fail"]
            out[stage] = {"pass": c["pass"], "fail": c["fail"],
                          "rate": round(c["pass"] / total, 3) if total else None}
        return out

    def health_alerts(self) -> list[str]:
        alerts = []
        s2 = self.counts["S2"]
        s2_total = s2["pass"] + s2["fail"]
        if s2_total >= 20 and s2["pass"] / s2_total < 0.20:
            alerts.append("S2 通过率 < 20%：疑似意图解析或提取器故障（而非真的没有职位）")
        s5 = self.counts["S5"]
        s5_total = s5["pass"] + s5["fail"]
        if s5_total >= 10:
            return alerts
        return alerts
