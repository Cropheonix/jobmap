# 数据源直取 POC 报告（2026-10-30）

> 目的：按「风控强度 × 数据价值」四层信源架构（T1 直取 / T2 索引 / T3 探测降级 / T4 人工在环）实测各数据源可达性，校准 JobMap 自动化能力的真实边界。测试 UA：`JobMap/1.0 (+local personal research)`，单次请求。

## 实测结果

| 数据源 | 层 | 结果 | 处置 |
|---|---|---|---|
| **谷德设计 gooood.cn/category/jobs/** | T1 | ✅ 200，107KB 实内容、无风控；**但职位数据由客户端 API 拉取**（SSR 只含城市筛选变体，`__NUXT__` 仅 563B 引导码） | 列表直取可达；职位批量获取需适配其公开 XHR 接口（站点无风控，接口大概率可直接调用）——列为下一步 |
| 谷德 interview-jobs 频道 | T1 | ✅ 200，40KB（部分 JS 加载） | 同上 |
| **Greenhouse 官方 API**（samsara board 实测） | T1 | ✅ 200，**319 个职位**（官方授权、无需认证） | OrgWatch 的 `ats_board=greenhouse:<token>` 已实现；Lever/Ashby 同模式扩展 |
| 国家公共招聘网 job.mohrss.gov.cn | T1 | ⚠️ https TLS 不兼容（EOF），**http 200 正常（66KB）** | Fetcher 已加 gov 站 http 降级重试 |
| 国聘 iguopin.com | T3 | ⚠️ SPA 壳页（1KB，JS 渲染） | 注册表标记 playwright；默认走 T2 索引 |
| archiposition.com | T3 | ❌ 403 | **T3 示范案例**：探测一次即永久降级为索引线索（`domain_access_state` 表） |
| BOSS直聘（连日多轮实测） | T2/T4 | ❌ httpx/无头/有头浏览器全部 JS 挑战（about:blank） | **定案：永不直连**。仅消费搜索索引快照 + 用户粘贴导入（T4） |
| 知乎（未登录） | T2/T4 | ❌ 403 | 同上 |
| SearXNG 上游引擎（google cse/ddg/brave） | T2 | ✅ 可用但限流敏感 | 已配齐：节流 2.5s + 引擎冷却 15min + 单任务 60 查询预算 + 连续限流提前终止 |

## 结论

1. **T1 直取层的实锤是官方 ATS 与企业官网**：Greenhouse 官方 API（319 职位/board、无需认证、允许使用）已实现为 OrgWatch 的 `ats_board` 通道；企业官网招聘页由 `recruiting_page` 监测（快照对比输出本周新增/下架）。垂直站中谷德列表页完全可达但数据在客户端 XHR 接口——无风控站点适配接口成本低，列为下一步。
2. **T3 探测降级机制必要且已实现**：archiposition 403、BOSS JS 挑战都会被「一次探测 → `domain_access_state` 永久降级 → 只走索引线索」策略吸收，不浪费请求也不加深指纹（含 WAF 壳页特征识别：标记词 + 剥标签后正文密度过低启发式）。
3. **T2 索引层是商业平台职位的唯一稳定通道**：平台为了让搜索引擎收录主动把结构化数据漏给索引，我们从索引读线索并如实标注「仅索引摘要/待核实」，符合「索引线索 ≠ 已核实在招」原则。许可边界：索引摘要按「暂时线索」处理（evidence 带许可注记），长期职位库建立在允许保存的企业自有公开数据、用户导入与授权接口之上。
4. **每周增量是正确节奏**：OrgWatch 快照对比把请求量降一个数量级；国家公共招聘网 https TLS 不兼容已由 gov 站 http 降级重试吸收。

## OrgWatch 真实运行记录（2026-10-30）

- 监测谷德招聘列表：可达、快照对比正常工作；首轮 added 40 条为城市筛选变体与静态资源 → 已加资源后缀与同路径变体排除规则；职位条目需其 XHR 接口适配（见下一步）。
- Greenhouse `samsara` board：319 职位解析正常（`_fetch_ats_board` 单测 + 真实连通双验证）。

## 下一步（按优先级）

1. **谷德 XHR 接口适配**（浏览器 F12 一次抓包即可定位，无风控）→ 设计领域 T1 批量源；
2. 建立成都目标企业目录（首批 20 家设计事务所：MUDA、JZ-LAB、a9a、简璞…）→ 填 `recruiting_page` → 每周 OrgWatch 增量；
3. 其他行业「谷德等价物」垂直站收录（每行业 1-2 个免风控站）；
4. Lever/Ashby ATS 适配器（与 Greenhouse 同模式）；
5. T2 摘要字段抽取精调（薪资 K/千/元三态、区域词表）。
