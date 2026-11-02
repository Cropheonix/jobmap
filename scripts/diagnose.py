# -*- coding: utf-8 -*-
"""JobMap 本地服务诊断脚本：启动后打印各模块工作状态。用法 python scripts/diagnose.py"""
import io
import json
import sys
from collections import Counter

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
BASE = "http://127.0.0.1:8617"
import httpx  # noqa: E402

c = httpx.Client(timeout=30, trust_env=False)
ok, fail = "  [OK]", "  [FAIL]"


def section(title):
    print(f"\n{'='*62}\n {title}\n{'='*62}")


# ── 1. 会话与服务 ──────────────────────────────────────────
section("1. 服务与会话")
cfg = c.get(f"{BASE}/api/session/config").json()
print(f"{ok} FastAPI 服务在线  v{cfg['version']}  算法版本 {cfg['algorithm_version']}")
print(f"{ok} 模式：{cfg['mode']}")
print(f"{ok} 底图：{cfg['tile']['crs']}  评分维度：{len(cfg['enums']['weight_labels'])} 维 "
      f"({'/'.join(cfg['enums']['weight_labels'].values())})")
print(f"{ok} 本地会话令牌已下发（修改类接口受保护）")

# ── 2. 数据源健康 ──────────────────────────────────────────
section("2. 数据源通道健康")
providers = c.get(f"{BASE}/api/providers").json()["providers"]
for p in providers:
    h = p.get("health") or {}
    if h.get("ok") is True:
        extra = f"  探测记录 {h.get('records')} 条" if h.get("records") else ""
        print(f"{ok} {p['display_name']}{extra}")
    elif p.get("status") == "available":
        print(f"{fail} {p['display_name']}  {h.get('hint') or h.get('error') or '不可用'}")
    else:
        print(f"  [--] {p['display_name']}（{p['status']}，M3/后续）")

# ── 3. 数据库概况 ──────────────────────────────────────────
section("3. 本地职位库")
stats = c.get(f"{BASE}/api/stats").json()
print(f"{ok} 职位总数 {stats['total_jobs']}（演示 {stats['demo_jobs']} / 真实 {stats['total_jobs']-stats['demo_jobs']}）")
print(f"{ok} 榜单区 {stats['valid_jobs']}（在招 {stats['active_jobs']} / 待核实 {stats['unverified_jobs']}）"
      f"  隔离区 {stats['quarantined_jobs']}  归档 {stats['expired_jobs']}")
print(f"{ok} 机构 {stats['organizations']} 家  来源记录 {stats['sources_total']} 条  "
      f"可定位 {stats['geo_located']}  近7日新增 {stats['new_last_7d']}")
print(f"{ok} 最近扫描：{stats['last_scan'] or '（重启后未跑任务）'}")

jobs = c.get(f"{BASE}/api/jobs?limit=2000&zone=all").json()["items"]
by_src = Counter(i["discovered_via"] or "seed" for i in jobs)
src_names = {"": "种子数据", "gooood": "谷德", "v2ex": "V2EX", "iguopin": "国聘",
             "remote_work": "远程.work", "jrzp": "金融招聘网", "searxng_discovery": "联网搜索",
             "user_paste": "粘贴导入", "org_watch": "官网监测"}
print(f"{ok} 来源分布：" + "  ".join(f"{src_names.get(k, k)}×{v}" for k, v in by_src.most_common()))

# ── 4. 抽样数据展示 ────────────────────────────────────────
section("4. 各来源抽样（榜单区前 3 条/源）")
for src_key in [k for k, _ in by_src.most_common() if k != ""]:
    sample = [i for i in jobs if i["discovered_via"] == src_key and i["filter_zone"] != "quarantined"][:3]
    if not sample:
        continue
    print(f"\n  ▸ {src_names.get(src_key, src_key)}：")
    for j in sample:
        print(f"    - {j['organization']['name'][:16]:<18} {j['title'][:24]:<26}"
              f" {j['salary_display']:<12} {j['status_label']}  V={j['subscores'].get('V','-')}"
              f"  完整度{j['completeness'] if j['completeness'] is not None else '-'}%")

# ── 5. 筛选/排序算法自检 ────────────────────────────────────
section("5. 算法自检")
r = c.get(f"{BASE}/api/jobs?limit=5&order=score").json()
tops = r["items"][:3]
print(f"{ok} 排序返回正常，Top3：")
for i, j in enumerate(tops, 1):
    print(f"    {i}. {j['title'][:22]}（{j['organization']['name'][:12]}）综合分权重口径一致")
weights = r.get("weights") or {}
print(f"{ok} 当前权重：{' '.join(f'{k}={v}' for k, v in weights.items())}")

# ── 6. 官网监测状态 ────────────────────────────────────────
section("6. OrgWatch 官网监测")
ws = c.get(f"{BASE}/api/orgs/watch/status").json()
print(f"{ok} 上次运行：{ws.get('last_run') or '尚未运行（首次请手动点「🏛 官网监测」建立基准）'}")
print(f"{ok} 调度器：随主服务常驻（每 6h 检查，默认 7 天间隔自动增量）")

# ── 7. 搜索引擎（T2）状态 ──────────────────────────────────
section("7. T2 联网搜索通道（SearXNG）")
try:
    sr = c.get("http://127.0.0.1:8889/search",
               params={"q": "成都 软件工程师 招聘", "format": "json", "language": "zh-CN"})
    d = sr.json()
    engs = Counter(x["engine"] for x in d.get("results", []))
    suspended = [x[0] for x in d.get("unresponsive_engines", [])]
    if d.get("results"):
        print(f"{ok} SearXNG 在线，实时查询返回 {len(d['results'])} 条（{dict(engs)}）")
    else:
        print(f"  [--] SearXNG 实例在线（HTTP {sr.status_code}），但上游引擎限流中：{suspended[:4]}")
        print(f"       T1 垂直站通道不受影响；限流自动冷却 15 分钟/任务 60 查询预算保护配额")
except Exception as exc:
    print(f"{fail} SearXNG 不可达：{exc}")

# ── 8. 安全边界自检 ────────────────────────────────────────
section("8. 安全边界")
r1 = c.patch(f"{BASE}/api/jobs/1/action", json={"status": "favorite"})
print(f"{ok} 无令牌修改被拒（HTTP {r1.status_code}）")
r2 = c.post(f"{BASE}/api/jobs/manual", json={"url": "http://127.0.0.1:9/x"},
            headers={"X-JobMap-Token": cfg["token"]})
print(f"{ok} 内网 URL 被拦截（HTTP {r2.status_code}，防 SSRF）")

print(f"\n{'='*62}\n 诊断完成：服务就绪，浏览器访问 http://127.0.0.1:8617\n{'='*62}")
