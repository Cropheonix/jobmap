# 主流搜索引擎接入与关键信息抓取 · 详细设计（M2.1/M2.2）

> 版本：2026-10-29 · 状态：设计稿。上承 [search-scheme-m2.md](search-scheme-m2.md)（总方案），本文回答两个问题：**① 能否调主流搜索引擎？怎么调？② 搜到之后如何抓取关键信息？** 确认后实施。

## 0. 结论：主流引擎的三条合规通道

| 通道 | 本质 | 费用 | 额度 | 中文质量 | 定位 |
|---|---|---|---|---|---|
| **① 自托管 SearXNG** | 本机实例把查询转发给 Google/Bing/Baidu 等 70+ 引擎并聚合 JSON | 免费 | 受上游引擎容忍度约束（个人家用 IP 低频 ≈ 人工搜索） | 好（可指定 baidu/bing 引擎） | **主力通道** |
| **② Google 官方 Custom Search API（PSE）** | 官方 JSON API | 免费 | 全网模式 100 次/天；**站点受限模式（引擎只搜指定站点）免费且不限次** | 好 | **增强通道**：把 zhipin/zhaopin/zhihu/gov 站点列表配置进 PSE，专门做按站点的职位/口碑发现，零成本无限额 |
| **③ 付费搜索 API**（博查 ~0.01 元/次 / 智谱 / Serper） | 商业授权索引 | 按次 | 稳定 | 好 | **兜底**：①被限流时自动切换 |

已排除：Bing Search API（2025-08 官方退役）、百度无公开官方 API、直接抓 Google/Baidu 结果页（违反 ToS + 验证码密集，违背项目「不规避」原则）、DuckDuckGo 非官方库（ToS 灰色）。

**要点**：通道②的站点受限免费模式与我们的查询形态（`site:zhipin.com 岗位 城市`）完全同构——把目标站点列表固化进 PSE 引擎配置后，查询词就是纯岗位词，不用再拼 `site:`，等于拥有一个官方授权的、免费用的「职位站垂直搜索 API」。需要用户用 Google 账号建一个 PSE（一次性 5 分钟配置，拿到 `cx` + `key` 存本机凭据库）。

## 1. 引擎路由矩阵（查询域 × 首选引擎）

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

## 2. 两阶段循环：发现 → 分诊 → 受限抓取

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

## 3. URL 分诊器与站点注册表

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

## 4. 抓取执行器

| 机制 | 设计 |
|---|---|
| 传输选择 | 默认 `httpx`（2s 连接/10s 总超时）；`fetcher: playwright` 仅白名单域名（知乎等 JS 页），并发 1、加载后等待选择器或 3s 上限 |
| 请求标识 | UA：`JobMap/1.0 (local personal research; +127.0.0.1)`，不带伪造浏览器特征 |
| 登录墙检测 | ① 30x 跳转至 `/login\|/signin\|passport\.` ② 页面 <20KB 且命中「登录后查看/请登录/扫码验证」③ meta refresh 指向登录域 → 标记 `login_wall`，证据降级为 SERP 摘要 |
| 验证码检测 | 状态 403/412/429 或正文命中「验证码/安全验证/异常流量」→ `captcha`，该域名进入退避 |
| 退避矩阵 | 429/503 → 30s/2m/10m 指数退避；连续 3 次失败 → 停源并在任务步骤中显示原因 |
| 内容上限 | 单页仅保留 ≤64KB 正文 + 提取字段；不保存整站、不保存图片/媒体 |

## 5. 关键信息提取（三级链）

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

## 6. 引擎健康池与降级链

```text
查询计划器 ─→ 路由矩阵选定引擎组
   bing → baidu → google        （SearXNG 内单引擎 429/空结果时顺序轮换）
        │ 连续 2 轮全组失败
        ▼
   Google PSE 站点受限通道（若已配置 cx/key）
        │ 未配置或仍失败
        ▼
   付费 API 兜底（若已配置 key）
        │ 仍失败
        ▼
   discovery_degraded：任务标记降级，界面与导出报告注明「本次发现层降级，结果可能不全」
```

引擎健康状态（每引擎：最近成功率、连续失败数、冷却截止时间）存内存 + `audit_events`，`/api/providers` 可查。

## 7. 一次「成都 后端」任务的完整时序（伪代码）

```python
plan = planner.build(keywords=["后端"], city="成都")          # 12 条职位查询 + 预算
for q in plan.job_queries:
    serp = discovery.search(q, engines=["bing","baidu"])      # 通道①/②自动选择
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

## 8. 默认参数表

| 参数 | 默认 | 说明 |
|---|---|---|
| `discovery.backend` | `searxng` | `127.0.0.1:8888`，可切 `pse` / `bocha` / `zhipu` |
| `discovery.pse.cx/key` | 空 | 通道②配置，存本机凭据库（不进仓库） |
| 任务发现层调用上限 | 200 次 | 单引擎 ≤60 次/小时 |
| 任务抓取页数上限 | 40 页 | 单域名 ≤30 |
| 抓取间隔 | 3s（商业站）/ 5s（gov） | 并发 1 |
| 自动核查公司数 | Top-20 | 流程条显示进度，可取消 |
| 证据缓存 | 发现 72h / 核查 7 天 | `observed_at` 判定 |

## 9. 新增决策点

| # | 决策 | 推荐 |
|---|---|---|
| D6 | 是否申请 Google PSE（免费，需 Google 账号一次性配置站点列表 + key） | **建议申请**：职位域白嫖官方不限额通道；不配置也能跑（SearXNG 主力） |
| D7 | Playwright JS 抓取白名单初版范围 | 仅 `zhihu.com` 与政府公告 JS 页；其余站点一律 httpx |
| D8 | 摘要即证据的口碑信号是否独立展示 | 是：「公司风险」面板区分「有原文核查」与「仅索引摘要」两级置信标识 |
