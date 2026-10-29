# 主流搜索引擎接入与关键信息抓取 · 详细设计（M2.1/M2.2）

> 版本：2026-10-29 · 状态：设计稿。上承 [search-scheme-m2.md](search-scheme-m2.md)（总方案），本文回答两个问题：**① 能否调主流搜索引擎？怎么调？② 搜到之后如何抓取关键信息？** 确认后实施。

## 0. 通道决策：零门槛开源优先（2026-10-29 已实测）

开源产品的用户不应被要求申请任何 key。通道按「零门槛 → 有门槛」排序，**默认体验完全免 key、免 Docker**：

| 通道 | 本质 | 用户需要做什么 | 状态 |
|---|---|---|---|
| **① 嵌入式 SearXNG（默认）** | JobMap 自带启用脚本：下载源码包 → pip 装进独立 venv → 以子进程托管在本机端口 → JSON API | 点一次「启用搜索」按钮 / 运行 `python scripts/enable_search.py` | ✅ **已在 Windows + Python 3.10 实测跑通**（见 0.1 实测记录） |
| ② 外部 SearXNG 实例 | 设置里填一个用户自己已有/信任的实例 URL（含自建 Docker 版） | 可选 | 已支持（配置项） |
| ③ 付费搜索 API（博查/智谱） | 商业授权索引 | 可选插件，默认隐藏，不引导申请 | 可选插件位 |
| ④ Google PSE 官方 API | 官方站点受限免费通道 | 需 Google 账号申请 cx/key —— **开源默认不接入**，仅作为 ③ 同级的可选插件保留文档 | 降级为可选插件 |

已排除：Bing Search API（2025-08 官方退役）、百度无公开官方 API、直接抓 Google/Baidu 结果页（违反 ToS，违背项目「不规避」原则）、DuckDuckGo 非官方库（ToS 灰色）。

### 0.1 实测记录（2026-10-29，Windows 10 x64 + Python 3.10.10）

- 安装路径：GitHub master tarball → `pip install -r requirements.txt` → `pip install . --no-deps`（setup.py 导入 searx 需先装依赖，鸡生蛋问题由启用脚本处理）。
- Python 3.10 需三个垫片（`scripts/searxng-embedded/shim/`，已入库）：`pwd`（valkeydb 顶层导入的 Unix 模块，limiter 关闭时不调用）、`tomllib`（3.11 入标准库，用 tomli 等价）、`sitecustomize` 补 `typing.NotRequired` 等。**Python 3.11+ 则只需 pwd 垫片**。
- 运行：`SEARXNG_SETTINGS_PATH=… PYTHONPATH=shim python -c "from searx.webapp import run; run()"`，werkzeug 监听 `127.0.0.1:8889`。
- JSON API 实测：查询 `成都 后端工程师 site:zhipin.com`（`format=json&language=zh-CN`）→ HTTP 200，**30 条结果**，google cse 与 duckduckgo 引擎响应正常，返回真实 BOSS直聘 职位列表页 URL；brave 因上游限流挂起（多引擎轮换设计正是为此）；HTML 模板缺失不影响 JSON API（嵌入式只用 JSON）。
- 结论：**零门槛、免 Docker、免 key 的默认搜索通道成立**。生产化封装见第 6 节。

## 1. 免 key 前提下的全面性与准确性工程

没有付费 API 不等于覆盖差。全面性来自**五个正交维度的叠加**，准确性来自**分诊与交叉验证**：

**覆盖的五个维度：**
1. **多引擎**：默认启用对程序化访问容忍度较高的引擎集合（bing、baidu、duckduckgo、qwant、moejek、startpage）；google 等高敏感引擎默认关闭、设置里可自行开启（开源产品的礼貌姿态，也降低验证码概率）。单引擎 429/空结果自动轮换（实测中 brave 限流未影响整体）。
2. **多查询矩阵**：岗位词 × 城市 × 区县 × 类别同义词 × `site:` 前缀，加 SERP 分页 1–3 页；查询规范化去重后按预算截断。
3. **站内搜索通道（不依赖搜索引擎）**：站点注册表为每个目标站声明 `search_url_template`（如政府人才网 `?keyword={kw}`、贴吧吧内搜索、企业官网站内搜索），robots 允许时直接查站内——新岗位比引擎索引更及时，且对引擎未收录的长尾站有效。
4. **列表页/RSS 直抓**：公共招聘网公告列表、企业官网招聘列表页、政府 RSS 直接抓（robots 允许），完全不经过搜索引擎。
5. **二轮种子扩展**：第一轮 SERP 摘要中提取的新公司名/岗位词 → 自动生成第二轮精确查询（公司名 + 口碑词/在招词），显著扩充口碑域召回；本地 FTS5 索引持续积累历史证据，越用越全。

**准确性的四道闸门：**
1. URL 分诊器只抓注册表认识的页面类型（第 4 节），`unknown` 只存摘要；
2. JSON-LD 结构化提取优先（第 7 节），正则兜底；
3. **多引擎交叉验证**：同一 URL/职位被 ≥2 引擎命中 → 可信度加分（进入 R 维度证据）；仅单引擎命中且仅有摘要 → 标注低置信；
4. 相关性阈值 + spam 过滤 + 公司主体消歧（USCC/全称+地区，见总方案第 6 节）。

## 2. 引擎路由矩阵（查询域 × 首选引擎）

SearXNG 支持按查询指定引擎（`/search?q=...&format=json&engines=bing,baidu`），路由在查询计划器内实现：

| 查询域 | 目标站点 | 首选引擎 | 理由 |
|---|---|---|---|
| 职位发现 | zhipin.com / zhaopin.com | bing → google | 职位详情页索引开放度最好；另走 PSE 站点模式 |
| 职位发现 | 前程无忧 / 猎聘 / 鱼泡 | baidu → bing | 中文招聘站索引偏百度 |
| 职位发现 | job.mohrss.gov.cn / 地方人才网 / 企业官网 | baidu | 政务与企业站索引 |
| 公司口碑 | zhihu.com | bing → google | 问答页索引充分 |
| 公司口碑 | tieba.baidu.com | baidu | 自家产品索引最深 |
| 公司口碑 | xiaohongshu.com | bing（仅取摘要） | 限制严格，摘要即证据 |
| 法律公告 | 各级人社/法院公告页 | baidu | 政务索引 |
| 法律记录 | creditchina.gov.cn | 不走引擎：官方 API 直连（USCC 精确查） | 权威且结构化 |

## 3. 两阶段循环：发现 → 分诊 → 受限抓取

```text
SERP 发现（只拿 title+snippet+url，摘要即证据，不抓原文）
   │
   ▼
URL 分诊器 Triage ──── 站点注册表：域名 → 页面类型 → 解析器 → robots 缓存结论
   │  ├─ job_page      （匹配 /job_detail/*.html 等模式）
   │  ├─ review_page   （知乎问题页/贴吧主题帖）
   │  ├─ legal_page    （公告详情页）
   │  └─ unknown       → 只存摘要证据，不抓
   │
   ▼ [robots 允许 ∧ 预算充足 ∧ 该域名未退避]
抓取执行器 Fetch ──── httpx（默认）/ Playwright（白名单 JS 页）；登录墙与验证码特征检测
   │
   ▼
关键信息提取 Extract ── 三级链：JSON-LD → 站点专属解析器 → Trafilatura 通用兜底
   │
   ▼
证据入库 ──────────── 复用 M1 去重合并；摘录带哈希、日期、原文链接、许可注记
```

**核心原则：分诊不过关的一律不抓**。SERP 摘要本身已可作为「口碑负面/职位存在性」的证据，只有需要结构化字段（薪资、坐标、发布日期）或超出摘要的正文时才发起抓取，单任务抓取量默认 ≤40 页。

## 4. URL 分诊器与站点注册表

```yaml
# backend/providers/site_registry.yaml（摘录）
zhipin.com:
  page_types:
    - pattern: "^/job_detail/[a-z0-9]+\\.html$"   # 无参数详情页（robots 实测允许）
      type: job_page
      parser: zhipin_job
      fetcher: httpx
  robots_verdict: allowed_subset        # 2026-10-29 实测
  login_wall_check: true
zhaopin.com:
  page_types:
    - pattern: "^/jobs?/[A-Za-z0-9_]+/?$"
      type: job_page
      parser: zhaopin_job
      fetcher: httpx
tieba.baidu.com:
  page_types:
    - pattern: "^/p/\\d+$"
      type: review_page
      parser: tieba_thread
      fetcher: httpx
zhihu.com:
  page_types:
    - pattern: "^/question/\\d+$"
      type: review_page
      parser: zhihu_question
      fetcher: playwright_whitelist     # 知乎部分内容 JS 渲染
creditchina.gov.cn:
  page_types: { api_direct: creditchina_api }
mohrss.gov.cn 及地方人才网:
  page_types:
    - pattern: "(/info|/notice|/gonggao)/"
      type: legal_or_gov_job
      parser: gov_notice
      fetcher: httpx
```

未注册域名 → `unknown`，只存摘要。注册表随版本更新，`robots_verdict` 记录实测日期，运行时以实时 robots 解析为准（缓存 7 天）。

## 5. 抓取执行器

| 机制 | 设计 |
|---|---|
| 传输选择 | 默认 `httpx`（2s 连接/10s 总超时）；`fetcher: playwright` 仅白名单域名（知乎等 JS 页），并发 1、加载后等待选择器或 3s 上限 |
| 请求标识 | UA：`JobMap/1.0 (local personal research; +127.0.0.1)`，不带伪造浏览器特征 |
| 登录墙检测 | ① 30x 跳转至 `/login\|/signin\|passport\.` ② 页面 <20KB 且命中「登录后查看/请登录/扫码验证」③ meta refresh 指向登录域 → 标记 `login_wall`，证据降级为 SERP 摘要 |
| 验证码检测 | 状态 403/412/429 或正文命中「验证码/安全验证/异常流量」→ `captcha`，该域名进入退避 |
| 退避矩阵 | 429/503 → 30s/2m/10m 指数退避；连续 3 次失败 → 停源并在任务步骤中显示原因 |
| 内容上限 | 单页仅保留 ≤64KB 正文 + 提取字段；不保存整站、不保存图片/媒体 |

## 6. 零门槛分发与嵌入式封装（enable_search）

开源用户的「启用搜索」必须是一键完成、零 key、零 Docker：

```text
scripts/enable_search.py（或界面「启用搜索」按钮）：
 1. 探测 Python 版本：≥3.11 → 干净安装；=3.10 → 附加 shim/ 垫片（pwd/tomllib/typing，实测有效）
 2. 在 .searchenv/ 创建独立 venv（不污染用户环境，卸载即删目录）
 3. 下载 SearXNG 官方 release tarball（缓存于 data/cache，可离线重装）→ pip install
 4. 生成 searxng_settings.yml（随机 secret_key、仅监听 127.0.0.1、开启 json 格式、
    关闭 limiter、默认引擎集合：bing/baidu/duckduckgo/qwant/mojeek，google 默认关）
 5. 以子进程方式随 JobMap 启停（run.py 托管，退出即回收，不留后台监听）
 6. 健康探测 /api/providers 显示「搜索通道：内置（在线/离线/未启用）」
```

| 场景 | 行为 |
|---|---|
| 用户机器无网络 | 复用 `data/cache` 的缓存包离线安装；无缓存则提示可稍后启用，其余功能不受影响 |
| 安装失败 | 明确报错原因（网络/版本/磁盘），JobMap 主功能完全可用，仅搜索通道标记「未启用」 |
| 用户已有自建实例 | 设置里填实例 URL 即可，跳过嵌入式安装 |
| 卸载 | 删除 `.searchenv/` 目录即彻底移除 |

仓库内已包含 `scripts/searxng-embedded/`（shim 垫片 + `searxng_settings.example.yml` + .gitignore），即本次实测所用的构件。

## 7. 关键信息提取（三级链）

**第一级：结构化标记（优先）**。大量职位页内嵌 schema.org `JobPosting` JSON-LD，可直接映射我们的字段且解析零歧义：

```jsonc
// <script type="application/ld+json"> 提取示例
{ "@type":"JobPosting", "title":"后端工程师",
  "hiringOrganization":{"name":"星澜科技"},
  "jobLocation":{"address":{"addressLocality":"成都","addressRegion":"四川","streetAddress":"..."}},
  "baseSalary":{"value":{"minValue":15,"maxValue":25,"unitText":"MONTH"}},
  "datePosted":"2026-10-18",
  "validThrough":"2026-11-18",      // 可直接判定「已截止」，喂给状态门控
  "employmentType":"FULL_TIME" }
```

**第二级：站点专属解析器**（JSON-LD 缺失时）。每站一个 `parser`：CSS 选择器 + 字段正则（薪资 `(\d+)-(\d+)K`、经验 `(\d+-\d+年|应届|应届/实习|不限)`、发布日期多格式）。首批实现：`zhipin_job`、`zhaopin_job`、`gov_notice`、`tieba_thread`、`zhihu_question`。

**第三级：通用兜底**。Trafilatura 抽正文 + 元数据（标题/日期/作者站点），再对正文跑字段正则与负面词表命中。只保存命中段落前后各 120 字的摘录。

**口碑负面词表（v1 本地规则，可解释可编辑）**：`欠薪/拖欠工资/裁员/辞退/逼退/仲裁/劳动监察/996/大小周/无偿加班/PUA/画饼/避雷/劝退/降薪/断缴/离职率`，命中记 `hit_keywords`；中性词不判负面（防止「不加班」误伤为「加班」，词表支持否定窗口 `不|无|没有` 前缀 2 字内豁免）。

**质量过滤**：结果相关性（标题/摘要与查询词的命中度 <30% 丢弃）、spam/广告特征（「急聘/日结/加微信」高频 + 无实质内容 → 标记 `spam_suspect` 不入信号）、SERP 与正文两处证据去重（URL 规范化复用 M1 dedup）。

## 8. 引擎健康池与降级链

```text
查询计划器 ─→ 路由矩阵选定引擎组
   bing → baidu → duckduckgo →（用户自行开启的 google 等）
        │ SearXNG 内单引擎 429/空结果时顺序轮换；连续 2 轮全组失败
        ▼
   站内搜索通道 + 列表页/RSS 直抓（不依赖引擎，见第 1 节维度 3/4）
        │ 覆盖仍不足
        ▼
   外部 SearXNG 实例（若用户在设置中配置）
        │ 仍失败且用户已自行配置付费 API 插件
        ▼
   可选付费插件（bocha/zhipu/pse —— 默认隐藏，不引导申请）
        │ 仍失败
        ▼
   discovery_degraded：任务标记降级，界面与导出报告注明「本次发现层降级，结果可能不全」
```

引擎健康状态（每引擎：最近成功率、连续失败数、冷却截止时间）存内存 + `audit_events`，`/api/providers` 可查。

## 9. 一次「成都 后端」任务的完整时序（伪代码）

```python
plan = planner.build(keywords=["后端"], city="成都")          # 12 条职位查询 + 预算
for q in plan.job_queries:
    serp = discovery.search(q, engines=["bing","baidu"])      # 嵌入式 SearXNG，免 key
    for hit in serp.results:
        t = registry.triage(hit.url)                          # job_page/review/legal/unknown
        if t.type == "job_page" and fetcher.allowed(t):
            page = fetcher.get(hit.url)                       # robots+登录墙检测
            rec = extractor.parse(page, chain=["jsonld", t.parser, "trafilatura"])
            jobs.upsert(rec)                                  # M1 去重合并
        else:
            evidence.save_snippet(hit)                        # 摘要即证据
top_orgs = ranking.top_n(20)
for org in top_orgs:                                          # 公司核查阶段（流程条第二行）
    for q in plan.review_queries(org):                        # 知乎/小红书/贴吧 ×2 组词
        ... → review_evidence（负面词命中、sentiment 规则分）
    legal.pull_creditchina(org.uscc or name)                  # 官方 API + 公告页
signals.emit(review_signal, legal_signal)                     # 第 9 节契约 → 排序 v2
```

## 10. 默认参数表

| 参数 | 默认 | 说明 |
|---|---|---|
| `discovery.backend` | `searxng_embedded` | 嵌入式实例 `127.0.0.1:8889`；可切 `searxng_external`（自填实例 URL）/ 可选插件 `bocha`/`zhipu`/`pse`（默认隐藏） |
| `discovery.default_engines` | bing, baidu, duckduckgo, qwant, mojeek | google 等高敏感引擎默认关闭，设置中一行开关自行开启 |
| 任务发现层调用上限 | 200 次 | 单引擎 ≤60 次/小时 |
| 任务抓取页数上限 | 40 页 | 单域名 ≤30 |
| 抓取间隔 | 3s（商业站）/ 5s（gov） | 并发 1 |
| 自动核查公司数 | Top-20 | 流程条显示进度，可取消 |
| 证据缓存 | 发现 72h / 核查 7 天 | `observed_at` 判定 |

## 11. 新增决策点

| # | 决策 | 推荐 |
|---|---|---|
| D6 | 付费/PSE 通道在开源版中的形态 | **默认隐藏为可选插件**（设置页「高级」内，不申请不提示），保证开箱零门槛 |
| D7 | Playwright JS 抓取白名单初版范围 | 仅 `zhihu.com` 与政府公告 JS 页；其余站点一律 httpx |
| D8 | 摘要即证据的口碑信号是否独立展示 | 是：「公司风险」面板区分「有原文核查」与「仅索引摘要」两级置信标识 |
| D9 | 默认引擎集合是否包含 google | **不包含**（开源产品礼貌姿态 + 降低验证码概率），设置里一行开关自行决定 |
