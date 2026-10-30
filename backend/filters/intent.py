"""S0 查询意图解析：把用户输入拆成结构化意图，是一切匹配的上游。"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

_SYNONYMS_PATH = Path(__file__).parent / "synonyms.yaml"


def load_synonyms(path: Path = _SYNONYMS_PATH) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


@dataclass
class Intent:
    raw: str
    role_terms: list[str] = field(default_factory=list)     # 岗位词组（已含同义词展开）
    primary_role: str = ""                                   # 首个岗位词（用于短语匹配）
    city: str = ""
    category: str = ""
    exclude_terms: list[str] = field(default_factory=list)
    unmatched: list[str] = field(default_factory=list)       # 无法归类的词，回显给用户

    def expanded_role_terms(self) -> list[str]:
        return self.role_terms


def _norm(text: str) -> str:
    return text.strip().lower()


# 泛岗位词：单独出现时不映射到任何特定同义词组（防止「工程师」被吸进「算法工程师」）
GENERIC_ROLES = {"工程师", "设计师", "经理", "助理", "专员", "教师", "老师", "分析师",
                 "架构师", "运营", "顾问", "实习生", "研究员", "职员"}


def parse_intent(text: str, synonyms: dict | None = None) -> Intent:
    syn = synonyms or load_synonyms()
    role_map: dict[str, list[str]] = {k: [k] + list(v) for k, v in (syn.get("role_synonyms") or {}).items()}
    city_aliases: dict[str, str] = {k.lower(): v for k, v in (syn.get("city_aliases") or {}).items()}
    role_category: dict[str, str] = syn.get("role_category") or {}

    intent = Intent(raw=text or "")
    tokens = [t for t in (text or "").replace("，", " ").replace(",", " ").replace("、", " ").split() if t.strip()]
    for token in tokens:
        t = _norm(token)
        if t.startswith("-") or t.startswith("－"):
            if len(t) > 1:
                intent.exclude_terms.append(t[1:])
            continue
        if t in city_aliases:
            intent.city = city_aliases[t]
            continue
        # 城市词直接命中（含「市」后缀归一）
        stripped = t[:-1] if t.endswith("市") and len(t) > 2 else t
        if _is_city_like(stripped):
            intent.city = stripped
            continue
        # 泛岗位词：保持原词，不进特定词组
        if t in GENERIC_ROLES:
            if t not in intent.role_terms:
                intent.role_terms.append(t)
            continue
        matched_role = False
        for main, variants in role_map.items():
            if any(v in t or t in v for v in variants if v):
                for v in [main, *variants]:
                    if v and v not in intent.role_terms:
                        intent.role_terms.append(v)
                matched_role = True
                break
        if matched_role:
            continue
        if t in (x.lower() for x in ["前端", "后端"]):
            continue
        intent.unmatched.append(token)
    # 未匹配词保留进 role_terms（作为普通关键词参与相关性），但不做主词
    for u in list(intent.unmatched):
        if u not in intent.role_terms:
            intent.role_terms.append(_norm(u))
    # 主词与类别提示（泛词也可作主词）
    if intent.role_terms and intent.role_terms[0] in GENERIC_ROLES:
        intent.primary_role = intent.role_terms[0]
    else:
        for main in role_map:
            if main in intent.role_terms:
                intent.primary_role = main
                intent.category = intent.category or role_category.get(main, "")
                break
    if not intent.primary_role and intent.role_terms:
        intent.primary_role = intent.role_terms[0]
    return intent


def _is_city_like(word: str) -> bool:
    # 轻量判断：非同义词库的城市词（仅支持常见大城，后续可用行政区划表替换）
    common = {"成都", "北京", "上海", "深圳", "广州", "杭州", "重庆", "武汉", "西安", "南京", "苏州", "长沙", "郑州", "天津"}
    return word in common
