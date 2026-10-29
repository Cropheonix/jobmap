# M2 多源搜索方案设计（v1 草案 · 待确认后实施）

> 版本：2026-10-29 · 状态：**设计稿，等待确认**。确认后按第 11 节里程碑实施，随后再设计排序算法 v2（消费第 9 节的信号契约）。
> 范围：职位发现（BOSS直聘/鱼泡/智联等 + 公共人力资源网站）、公司口碑（小红书/贴吧/知乎等）、公司法律与信用风险三类信号的获取方案。
> 配套详细设计：[discovery-fetch-design.md](discovery-fetch-design.md)（搜索引擎接入通道与「搜索→抓取→关键信息提取」全链路）。

## 0. 结论摘要

- **四层架构**：查询计划器 → 发现层（**默认自托管 SearXNG，开源免费、无需 key**；可插拔付费 API 兜底）→ 抓取层（robots 感知 + httpx/Trafilatura/按需 Playwright，仅公开页）→ 解析与证据层。登录墙/验证码/付费内容一律标记 `access_restricted`，绝不规避。
- **实测 robots.txt（2026-10-29）**：BOSS直聘与智联的**无参数职位详情页对通用爬虫开放**，可低频抓取；鱼泡、小红书限制严格，走「发现层摘要即证据 + 用户导入」；知乎/贴吧按运行时 robots 判定低频抓取。
- **发现层默认自托管 SearXNG**（AGPL-3.0，Docker 一键部署，开启 `search.formats: [html, json]` 后即得免费 JSON 搜索 API，可聚合 Google/Bing/Baidu 等 70+ 引擎），零按次费用；博查/智谱等付费 API 作为可选兜底；Bing Search API 已于 2025-08 退役，排除。
- **法律与信用**：优先「信用中国」公开数据服务 + 人社部门欠薪黑名单等政府公开公告 + 用户导入企查查/天眼查报告；裁判文书网因登录/验证码墙不做自动化，仅用户辅助。公司主体用「统一社会信用代码 > 全称+地区」消歧，低置信度匹配不参与评分。
- **对排序的输出**：每家公司产出 `review_signal` 与 `legal_signal` 两组结构化信号（第 9 节契约），作为下一阶段排序算法 v2 的输入。

## 1. 设计原则与合规红线（承自主方案 4.1–4.3）

1. **不规避访问控制**：遇到登录、验证码、429、反爬质询，立即停止该源并标记 `access_restricted`，不伪装成功、不轮换代理、不伪造 UA 身份（UA 标识 `JobMap/1.0 (+local personal research)`）。
2. **robots.txt 即契约**：抓取层对每个域名先取 robots 并缓存（7 天），用标准解析器判定；未取到 robots 的站点按「仅允许已确认公开页」处理并记录探测结果。
3. **发现层合规**：搜索引擎摘要通过正规付费 API 获取（API 服务自身持有索引授权），只保存标题/摘要/URL/日期/来源站点，摘要即证据，不绕过原文版权。
4. **不收集个人信息**：口碑与法律信号只存**帖子/案件级元数据**（标题、日期、互动量、负面关键词命中、案号、案由、公司方名），不存发帖人/当事人身份字段。
5. **频率礼貌**：单域名并发 1、间隔 ≥3s（gov 站 ≥5s）、每任务每域名 ≤30 次请求；全局每日请求预算可配置。
6. **风险提示非定论**：口碑与法律信号在 UI 与导出一律标注为「风险提示/未经核实的公开信息聚合」，不构成对公司的事实认定（承主方案风险 #4 公平性原则）。

## 2. 信号域划分

| 域 | 内容 | 主要来源 | 更新触发 |
|---|---|---|---|
| A 职位发现 | 岗位卡片（标题/公司/薪资/地点/发布时间/原文链接） | BOSS直聘、智联、鱼泡、前程无忧、猎聘、国家/地方公共招聘网、企业官网 | 用户搜索任务 |
| B 公司口碑 | 员工/求职者评价帖元数据 + 本地情感规则打分 | 知乎、小红书、百度贴吧、脉脉公开页、看准网公开页 | 职位任务完成后对 Top-N 公司自动；单公司手动 |
| C 法律与信用 | 劳动仲裁/合同纠纷/被执行/行政处罚/欠薪黑名单 | 信用中国、中国执行信息公开网、人社公告、用户导入企查查/天眼查 | 同 B |

## 3. 平台接入矩阵（robots 实测：2026-10-29）

| 平台 | 发现层（搜索API `site:` 检索） | 抓取层（直接取页） | 实测 robots 结论 | 降级路径 |
|---|---|---|---|---|
| BOSS直聘 zhipin.com | ✅ `site:zhipin.com {岗位} {城市}` 取索引标题+摘要 | ✅ 仅无参数职位详情页 `/job_detail/*.html`，低频 | 通用 UA 允许无 query 的详情页；禁止带参数列表页、`/job_detail/l*.html`、`/sem/`、用户页 | 登录墙→`access_restricted`，保留索引摘要 |
| 智联招聘 zhaopin.com | ✅ 同上 | ✅ 仅无参数职位页 | 通用 UA 禁一切带 `?` 的 URL、`/user/`、`/api/`；裸职位页允许 | 同上 |
| 鱼泡网 yupao.com | ✅（蓝领岗位索引） | ❌（仅 `/topic/`、`/article/` 允许） | 大量 Disallow，职位页基本禁抓 | 发现层摘要 + 用户导入 |
| 前程无忧 51job / 猎聘 liepin / 拉勾 lagou | ✅ | 运行时探测 robots 后决定 | 本次探测未返回，实现时白名单确认 | 发现层摘要 + 用户导入 |
| 国家公共招聘网 job.mohrss.gov.cn 及地方人才网（成都人才网、四川人才网、高校就业网） | ✅（或直接抓列表页） | ✅ 政府公开公告，优先源（主方案 4.1 优先源） | 本次探测未返回（gov 站对非浏览器 UA 敏感），实现时以浏览器式请求+人工确认 | 用户导入 |
| 企业官网招聘页 | ✅ | ✅ robots 允许时 | 逐站判定 | 发现层摘要 |
| 知乎 zhihu.com | ✅ `site:zhihu.com {公司名} 怎么样/裁员/欠薪` | ⚠️ 运行时 robots 判定，仅公开问答页 | 对 Googlebot 开放内容页、禁 `/search`；通用规则需运行时解析 | 摘要即证据 |
| 小红书 xiaohongshu.com | ✅ `site:xiaohongshu.com {公司名} 避雷/入职` | ❌ | 对主流引擎仅放行首页/explore；限制严格 | 摘要即证据（标题+片段对口碑判断已足够） |
| 百度贴吧 tieba.baidu.com | ✅ `site:tieba.baidu.com {公司名}吧` | ⚠️ 运行时 robots 判定，仅主题帖页 | 按 UA 分规则，通用规则需运行时解析 | 摘要即证据 |
| 看准网 kanzhun.com / 脉脉公开页 | ✅ | ⚠️ 运行时判定（登录墙常见） | 待探测 | 摘要即证据 |
| 信用中国 creditchina.gov.cn | — | ✅ 官方数据服务 API（需申请 key）+ 公开页 | 政府公开数据 | 用户导入 |
| 中国执行信息公开网 zxgk.court.gov.cn | — | ⚠️ 公开查询但常带验证码 | — | 验证码→用户辅助查询+导入 |
| 裁判文书网 wenshu.court.gov.cn | — | ❌ 登录+验证码墙（2021 起严控） | — | 不做自动化；用户辅助检索后导入案件元数据 |
| 企查查/天眼查/爱企查 | — | ❌（付费 API/登录） | — | 用户复制报告→结构化导入（JSON/CSV/粘贴解析） |

## 4. 四层架构

```text
┌─ 查询计划器 QueryPlanner ────────────────────────────────┐
│ 用户输入（岗位词×城市×类别） ──→ 职位查询矩阵              │
│ 榜单 Top-N 公司 ──→ 口碑查询矩阵 × {知乎,小红书,贴吧}      │
│ 公司主体（USCC/全称+地区）──→ 法律查询矩阵                 │
│ 输出：带预算上限的查询队列（去重、截断）                    │
└──────────────┬───────────────────────────────────┘
               ▼
┌─ 发现层 DiscoveryBackend（可插拔）────────────────────────┐
│ 默认：自托管 SearXNG（Docker，JSON API，免 key 免费）      │
│ 备选：Bocha / 智谱 web-search / Brave / Tavily（自带 key） │
│ 输出：{title, snippet, url, site, date?}[]，摘要即证据      │
└──────────────┬───────────────────────────────────┘
               ▼
┌─ 抓取层 Fetcher（robots 感知）────────────────────────────┐
│ httpx 请求 + Trafilatura 正文抽取 + 按需 Playwright（JS 页）│
│ robots 缓存7天 → 检查 → 单域名并发1/间隔3s/超时10s          │
│ 429/403/登录重定向 → access_restricted + 指数退避停源      │
└──────────────┬───────────────────────────────────┘
               ▼
┌─ 解析与证据层 ────────────────────────────────────────────┐
│ 站点 schema 解析器（zhipin/zhaopin/gov/知乎…）             │
│ 字段映射 → 去重（复用 M1 dedup）→ 证据快照（摘要+日期+URL） │
│ 口碑情感规则引擎（本地负面词表）→ review_signal             │
│ 法律记录结构化 → legal_signal                              │
└───────────────────────────────────────────────────┘
```

## 4.5 开源选型对比（2026-10 调研结论）

### 发现层：怎么「直接接搜索引擎」且免费

| 方案 | 原理 | 优点 | 缺点 | 结论 |
|---|---|---|---|---|
| **SearXNG 自托管（嵌入式）**（AGPL-3.0，11K+ stars） | 元搜索：本机实例转发查询到 Google/Bing/Baidu 等 70+ 引擎并聚合 | **免 Docker 免 key**：enable_search 脚本 pip 装进独立 venv 并以子进程托管（已在 Windows+Py3.10 实测跑通，Python 3.10 需 3 个垫片、3.11+ 仅 1 个）；JSON API；中文可用百度/必应引擎 | 上游引擎对高频调用会限流/验证码；公共实例不开 JSON | ✅ **默认方案**。个人低频家用 IP 使用等同人工搜索，合规风险低（详见 [discovery-fetch-design.md](discovery-fetch-design.md)） |
| Google PSE 官方 API | 官方 JSON API | 站点受限模式免费不限次 | **需 Google 账号申请 cx/key —— 开源默认不接入** | ⚠️ 降级为隐藏的可选插件（与付费 API 同级） |
| YaCy（P2P 自建索引） | 自己爬+分布式索引，完全不依赖第三方 | 完全自主 | 索引规模与中文搜索质量不足 | ❌ 不作主力，预留为离线检索实验 |
| Whoogle | Google 前端代理 | 轻量 | 单引擎、稳定性一般 | ❌ |
| 直连 DuckDuckGo（`ddgs` 等非官方库） | 抓 DDG HTML 端点 | 免费 | 无官方 API、违反其 ToS 风险、中文弱 | ❌ 与本项目合规原则冲突 |
| 直连 Baidu/Google SERP 抓取 | 解析结果页 HTML | — | 违反 ToS、验证码密集 | ❌ |
| Brave / Mojeek 免费 API | 官方免费额度（Brave 免费 2000 次/月） | 合规 | 额度有限、中文质量一般 | ⚠️ 备选 |
| Bocha / 智谱 web-search 付费 API | 商业授权索引 | 稳定、中文好、有摘要增强 | 按次付费 | ⚠️ **兜底备选**（SearXNG 被限流时切换） |

**许可证说明**：SearXNG 为 AGPL-3.0，JobMap（MIT）**不捆绑、不修改**它，仅通过 HTTP JSON API 调用用户自行部署的实例，无许可证传染问题；部署方式与归属在 NOTICE.md 声明。

### 抓取/解析层：通用爬虫选什么

| 方案 | 定位 | 对本项目的适配度 |
|---|---|---|
| Scrapy（BSD） | 全功能爬虫框架（Spider/Pipeline/调度） | 功能过剩：本项目是「低频礼貌抓取」不是建库爬虫，引入整个框架增加复杂度 |
| Crawlee（Apache-2.0，Python/JS） | 现代爬虫框架，自带队列/自动扩缩/Playwright 集成 | 同上略轻，仍偏服务端批量场景 |
| **httpx + Trafilatura + 按需 Playwright**（推荐） | 按主方案既定路线：普通请求优先，Trafilatura 做正文/元数据抽取（优于裸 BeautifulSoup），JS 渲染页才起 Playwright | ✅ 与 M1 技术栈连续、依赖最少、礼貌策略自控 |

结论：**不引入重型爬虫框架**，在现有 `pipeline/` 内扩展 `fetcher.py`（httpx + robots + 退避）与 `extractor.py`（Trafilatura），Playwright 仅对白名单内的 JS 依赖公开页启用（如部分政府公告页）。



## 5. 查询计划器

**职位查询矩阵**：`{岗位词} {城市}` × 平台 `site:` 前缀 × 类别词扩展（如「设计」→ 商业空间/室内/景观）。每任务上限：平台数 × 关键词组合 ≤ 40 条查询（可配置）。查询词去重（规范化后同词只发一次）。

**口碑查询矩阵**（每公司）：`site:zhihu.com|site:xiaohongshu.com|site:tieba.baidu.com {公司名} {怎么样|裁员|欠薪|加班|避雷|仲裁}` → 每公司 3 站点 × 2 组词 = 6 条，取前 10 条结果。

**法律查询矩阵**（每公司）：`{公司全称} {被执行|劳动仲裁|行政处罚|欠薪}` + 信用中国 API 精确检索（USCC/全称）。公司名歧义防护：查询带地区限定词；结果页出现多主体时标记 `ambiguous`。

**预算**：SearXNG 后端无按次费用，预算控制改为「上游礼貌预算」——全局每任务发现层调用 ≤200 次、单引擎 ≤60 次/小时（避免触发上游限流）；使用付费兜底 API 时另计金额上限（默认 ¥5/任务）。超出时优先保证职位域，口碑/法律标记「预算不足未核查」。

**SearXNG 部署（Windows）**：`scripts/searxng/docker-compose.yml` + 预置 `settings.yml`（开启 JSON 格式、关闭 limiter（仅监听 127.0.0.1）、启用 baidu/bing/google/duckduckgo 引擎）。前提：Docker Desktop；无 Docker 环境时该后端显示「未部署」，自动提示切换付费 API 后端。JobMap 内置 `/api/providers` 健康检查探测 `http://127.0.0.1:8888/search?q=test&format=json`。

## 6. 公司核查管线（B+C 域编排）

- **自动**：职位任务完成、榜单生成后，对**Top-N 公司（默认 20，可配置）**自动触发核查；流程条扩展第二行「公司核查 k/N」。逐公司串行、逐域名限速，可取消。
- **手动**：卡片/公司详情页「🔍 深度核查」按钮，对单公司全量执行。
- **缓存**：核查结果 7 天内复用（`checked_at` 判定），重复任务不重复消耗预算。
- **主体消歧**：organization 记录 `uscc`（统一社会信用代码）与注册地区；匹配优先级 USCC > 全称精确 > 全称+地区。`match_confidence < 0.7` 的法律/口碑记录**只展示不参与评分**。

## 7. 数据模型扩展（在 M1 九表基础上）

```sql
organizations 新增: uscc TEXT, registered_region TEXT
CREATE TABLE review_evidence (
  id, organization_id, source(zhihu/xhs/tieba/kanzhun/other),
  url, title, snippet, published_at?, engagement?,   -- 点赞/评论数（若索引可得）
  sentiment TEXT,        -- negative/neutral/positive（本地规则判定）
  hit_keywords JSON,     -- 命中负面词：["欠薪","加班"]
  match_confidence REAL, observed_at, is_demo BOOL
);
CREATE TABLE legal_records (
  id, organization_id, source(creditchina/zxgk/hr_bulletin/user_import),
  record_type,           -- labor_arbitration/contract_dispute/enforcement/admin_penalty/wage_blacklist
  case_no?, title, summary, amount?,                 -- 标的额（若有）
  filing_date?, status?, match_confidence REAL,
  url, observed_at, raw_import TEXT?
);
job_sources 新增: fetch_status(accessible/login_wall/captcha/restricted), robots_checked_at
search_tasks 扩展: phase ENUM(job_discovery/company_enrichment), budget_json
providers 表（运行时）新增 discovery_backend 配置项（加密存本机凭据库，不进仓库）
```

## 8. 频率、缓存与退避

| 机制 | 规则 |
|---|---|
| 域名限速 | 并发 1；间隔 3s（商业站）/ 5s（gov 站）；每任务每域名 ≤30 请求 |
| 退避 | 429/503 → 指数退避（30s/2min/10min），连续 3 次失败停源 |
| 登录墙检测 | 响应含登录重定向/`passport`/验证码特征 → `login_wall`，不计为抓取成功 |
| 内容缓存 | 页面正文缓存 72h（哈希去重），职位卡片 24h，核查证据 7 天 |
| 日志 | 全部请求（时间/URL/状态/耗时/robots 结论）入 `audit_events`，「运行日志」页可查 |

## 9. 输出信号契约（供排序算法 v2 消费——下一阶段设计）

```jsonc
review_signal = {
  org_id, docs: 12, neg_ratio: 0.42, sentiment: 62,     // 0-100，100 最好
  last_date: "2026-10-21", hit_keywords: ["加班"],       // 按频次排序
  source_breakdown: { zhihu: 5, xhs: 4, tieba: 3 },
  confidence: 0.9, incomplete: false                     // 无任何结果→incomplete=true
}
legal_signal = {
  org_id, labor_arbitration: 2, contract_dispute: 1, enforcement: 0,
  admin_penalty: 0, wage_blacklist: false,
  recent_weighted: 1.4,                                  // 时间衰减后的加权量（近2年全额，逐年×0.5）
  last_date: "2026-08-02", confidence: 0.85, incomplete: false
}
```

**排序侧使用原则（预约束，细节在排序 v2 设计中定）**：a) 展示为「风险提示」而非定论；b) `confidence < 0.7` 不参与评分；c) 权重面板提供「口碑/法律信号」总开关与独立权重（用户可整体关闭以保证公平性偏好）；d) 无证据（incomplete）取中性，不因「查不到」而加分或扣分。

## 10. 决策点（请拍板）

| # | 决策 | 选项 | 推荐 |
|---|---|---|---|
| D1 | 发现层默认后端 | a) **嵌入式 SearXNG**（免 Docker 免 key，enable_search 一键启用，已实测）b) 用户已有的外部 SearXNG 实例（填 URL）c) 可选付费/PSE 插件（默认隐藏） | **a 为默认**；开源开箱零门槛，付费通道不引导不提示 |
| D2 | 法律数据主通道 | a) 信用中国 API（免费需申请）b) 用户导入企查查/天眼查为主 c) 两者并重 | **c**：API 做底线，导入做深度 |
| D3 | 口碑情感分析 | a) 本地负面词表规则（零成本可解释）b) 本地小模型（效果好需依赖）c) 云端 LLM | **a 起步**，预留 b 升级位；不上云 |
| D4 | 自动核查范围 | Top-N（默认 20）公司自动 vs 全部手动 | **默认 20 可配置** |
| D5 | 平台优先级 | 第一批接入哪几个职位平台 | **BOSS直聘 + 智联 + 国家公共招聘网**（robots 已实测友好），鱼泡/51job/猎聘 第二批 |

## 11. 实施里程碑与验收

| 阶段 | 内容 | 验收标准 |
|---|---|---|
| M2.1 发现层 | DiscoveryBackend 抽象 + **SearXNG 适配器（默认）** + Bocha/智谱适配器（兜底）+ 查询计划器 + `scripts/searxng/` 部署脚本与健康探测 | 输入「成都 后端」返回带证据的索引结果；SearXNG 未部署时明确提示降级路径 |
| M2.2 职位抓取 | robots 感知 Fetcher + zhipin/zhaopin 解析器 + 登录墙检测 | 详情页字段入库且带 `robots_checked_at`；遇登录墙正确标记 |
| M2.3 公司核查 | review_evidence/legal_records 表 + 口碑规则引擎 + 信用中国接入 + 企查查导入格式 | Top-20 公司自动核查可在流程条观察；同名歧义公司不误伤 |
| M2.4 信号契约 | 信号聚合 API + UI「公司风险」面板 + 导出报告含风险提示章节 | 排序 v2 可直接消费第 9 节契约 |

确认本方案（或提出修改）后，开始**排序算法 v2 设计**：在现有 M/P/C/R/D 五维之上，评估将口碑与法律信号并入 R（可信度）或新增第六维「风险」的方案对比。
