"""筛选核心算法包（Filter Algorithm v1，对应 docs/filter-algorithm-design.md）。

S0 意图解析 → S1 字段校验 → S2 语义相关性 → S3 实体消歧 → S4 状态门控 → S5 置信度。
输出三区处置：accepted / accepted_suspect / quarantined + filter_trace。
"""
from backend.filters.pipeline import FilterResult, run_filters  # noqa: F401
