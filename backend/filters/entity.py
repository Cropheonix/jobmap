"""S3 实体消歧：公司主体匹配置信度（USCC > 全称+地区 > 全称 > 规范化简称 > 显示名相似）。

设计要点：entity_conf < 0.75 时公司级信号（口碑/法律）不挂载，防同名公司误伤。
"""
from __future__ import annotations

from backend.pipeline.dedup import normalize_company, title_similarity


def entity_confidence(*, record_name: str, record_uscc: str | None, record_region: str,
                      org_name: str | None, org_uscc: str | None, org_region: str | None) -> tuple[float, bool]:
    """返回 (置信度, 是否存在同名歧义)。

    org_* 为数据库中已匹配到的机构（可为 None 表示新机构）。
    """
    if record_uscc and org_uscc:
        return (1.0, False) if record_uscc == org_uscc else (0.0, False)
    name, oname = (record_name or "").strip(), (org_name or "").strip()
    if name and oname:
        if name == oname:
            conf = 0.9 if (record_region and org_region and record_region == org_region) else 0.75
            return conf, False
        n1, n2 = normalize_company(name), normalize_company(oname)
        if n1 == n2:
            return 0.6, False
        if title_similarity(name, oname) >= 0.85:
            return 0.5, False
    # 新机构（库中无匹配）：按记录自身证据强度估计
    if record_uscc:
        return 0.9, False
    if record_region:
        return 0.75, False
    return 0.6, False


def is_ambiguous(record_name: str, same_name_orgs_count: int) -> bool:
    """一次搜索中同一规范化名对应多个不同主体 → 歧义。"""
    if same_name_orgs_count <= 1:
        return False
    return normalize_company(record_name or "") != ""
