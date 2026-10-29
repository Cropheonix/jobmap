"""全局配置与领域常量。"""
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "jobmap.db"
SAMPLE_DATA_PATH = BASE_DIR / "sample-data" / "sample_jobs.json"
FRONTEND_DIR = BASE_DIR / "frontend"

APP_NAME = "JobMap 求职情报地图"
APP_VERSION = "1.0.0"
ALGORITHM_VERSION = "1.0.0"

# 评分维度：M 岗位匹配 / P 薪资 / C 团队与项目 / R 来源可信度与时效 / D 通勤距离
DEFAULT_WEIGHTS = {"M": 30, "P": 25, "C": 20, "R": 15, "D": 10}
WEIGHT_LABELS = {"M": "岗位匹配度", "P": "薪资待遇", "C": "公司团队实力", "R": "来源可信度", "D": "通勤距离"}
MISSING_NEUTRAL_SCORE = 50  # 缺失维度的中性分，绝不等价于零分

RANKING_MODES = {
    "score": "综合推荐",
    "team": "小团队优先",
    "newest": "最新发布",
    "salary": "薪资最高",
}

ORG_TYPES = {
    "studio": "工作室/事务所",
    "company": "专业公司",
    "enterprise": "大型企业/集团",
    "institute": "机构/事业单位",
    "unknown": "未知类型",
}
ORG_TYPE_GROUP_ORDER = ["studio", "company", "enterprise", "institute", "unknown"]

JOB_STATUS = {
    "active": "在招",
    "unverified": "状态未核实",
    "expired": "已截止",
    "closed": "已下架",
}

LOCATION_PRECISION = {
    "building": "门牌/楼宇",
    "block": "街区/园区",
    "district": "行政区示意",
    "unlocatable": "无法定位",
}

ACTION_STATUSES = {
    "none": "未标记",
    "favorite": "收藏",
    "todo": "准备投递",
    "applied": "已投递",
    "interview": "面试",
    "offer": "Offer",
    "rejected": "不考虑",
}

JOB_CATEGORIES = [
    "互联网/软件", "金融/财务", "教育/科研", "医疗健康", "制造/工业",
    "建筑/地产", "设计创意", "销售/市场", "职能/法律/人力", "供应链/物流", "其他行业",
]

SOURCE_TYPES = {
    "official_site": "公司官网",
    "gov_public": "公开招聘公告",
    "platform_public": "招聘平台公开页",
    "aggregator": "聚合索引",
    "user_import": "用户导入",
    "sample": "内置示例",
}

# 默认底图：OpenStreetMap 标准瓦片（遵循其使用政策，见 NOTICE.md）
DEFAULT_TILE_URL = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
DEFAULT_TILE_ATTRIBUTION = '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> 贡献者'

# ── M2：搜索通道与筛选 ───────────────────────────────────────────
# 嵌入式 SearXNG（enable_search.py 安装；默认引擎集合不含 google——开源产品礼貌姿态）
SEARXNG_EMBEDDED_URL = "http://127.0.0.1:8889"
DEFAULT_SEARCH_ENGINES = ["bing", "baidu", "duckduckgo", "qwant", "mojeek"]
SEARCH_CHANNEL_CONFIG = BASE_DIR / "data" / "search-channel.json"

# 源类型可信度（筛选 S5 与评分 R 共用口径）
SOURCE_TYPE_TRUST = {
    "official_site": 1.0, "gov_public": 0.95, "platform_public": 0.70,
    "aggregator": 0.55, "user_import": 0.40, "sample": 0.35,
}
