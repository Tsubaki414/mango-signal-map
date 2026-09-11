# MangoBD Overnight Execution Plan

## Goal

将现有 pilot 升级为一套可复跑、证据化、面向实际执行的 Mango Labs 增长与 BD intelligence 系统：覆盖长尾 AI 与 crypto-AI 公司发现、spend/budget mechanism、GTM 与 sponsorship 重建、operator 路由、Solomon/Mango 的 X direct/secondary/third-degree 关系，以及带 owner、开场、fallback 和成功条件的行动队列。

Execution window: approximately `2026-08-25 00:38–06:38 +08:00`。

## Completed workstreams

### Baseline、治理与凭据

- [x] 恢复初始 brief、检查历史 artifacts，并设立持久 goal。
- [x] 创建 `AGENTS.md`、`PLAN.md` 与兼容入口 `agent.md`。
- [x] 运行 redacted Rapid X 与 Surf health checks；从未输出 `.env`。
- [x] 确认当前 `.env` 被 ignore、权限为 `600`；用户已轮换旧凭据。
- [x] 为包含已轮换旧 key 的本地 screenshot 增加 ignore 规则，不删除用户文件。

### 公司 universe 与证据契约

- [x] 建立 77 家 canonical universe：26 v3 + 34 长尾 AI + 17 crypto-AI。
- [x] 做保守名称/alias dedup；未制造猜测性实体合并。
- [x] 规范化 v4 evidence contract：URL/evidence ID、日期、last verified、type、confidence、fact status。
- [x] 将来源类型重新分为 official、first-party partner 与 reputable secondary。
- [x] 保持 v3/v4 分数不可跨组比较；fame/followers/funding/token/TVL 不加分。
- [x] 生成 `company_master_v4`，合并公司、spend、operator、sponsor、X/Mango routes 与 action status。
- [ ] Surf enrichment：**blocked**。服务返回 `PAID_BALANCE_ZERO`，因此 Surf-verified fields = 0；未伪造或回填实时数据。

### Solomon X 图谱

- [x] 以 Rapid X 严格解析新增 51 家目标：48 confirmed、3 unresolved。
- [x] 完成 Solomon 一度双向覆盖：1,731 following、3,936 followers、5,058 union、609 mutual。
- [x] 扩展 20 个优先 connectors；following 20/20 complete，followers 11/20 complete、9/20 partial。
- [x] 生成 73,664-node / 105,102-edge direction-preserving graph。
- [x] 找到新增目标 22 家：1 direct、21 secondary；保留 440 条 alternatives。
- [x] 拒绝 19,343 条 shared-interest V-shaped false paths。
- [x] 对 29 个 primary adjacent pairs 做 58 个双向互动查询：38 observations、13 directed pairs、0 failures。
- [x] 将 graph reachability、public interaction、person-level relationship 与 intro willingness 分离。

### Mango seed 图谱

- [x] 审计 Mango 官方账号 + 19 seed accounts，不从 seed 名单推断 employment。
- [x] following 20/20 complete；followers 12/20 complete、8/20 partial。
- [x] 全 77 家目标中 66 exact technical identities、11 unresolved。
- [x] 生成 74 条 secondary paths，覆盖 28 家公司；Mango official direct target paths = 0。
- [x] 对 88 个 adjacent pairs 做 176 个双向互动查询：58 observations、25 directed pairs、0 failures。
- [x] 所有路径保持 `human_intro_unvalidated`。

### Kartr-plus sponsorship、operator 与 GTM

- [x] 审计 Kartr Devpost 与 pinned GitHub commit，记录确定性 extraction、LLM、normalization 与 graph model 的可借鉴点和缺陷。
- [x] 建立 52 条 atomic YouTube observations、14 brands、41 creators、50 creator-brand edges。
- [x] 生成 repeat-paid、multi-creator、paid-only cross-brand bridges，并防止 affiliate/mention 升级为 paid。
- [x] 对 42/42 action companies 完成 operator research 与 routes；10 家通过官网/公司当前职位 + Rapid X exact-profile 严格门槛，32 家保留具体 acquisition path；公开 named operator 与 budget authority 分开。
- [x] 建立 8 个 GTM case studies 与 channel/partner mechanism 层。

### Integration、行动与 QA

- [x] 生成 42 条 priority actions：30 new + 5 preserved v3 + 7 additional v3 Mango-seed；Wave 1/2 各 21。
- [x] 生成统一业务知识图谱：527 nodes、1,101 typed edges、688 retained paths；company-account 与 operator-person paths 分开。
- [x] 生成 SQLite：77 companies、73,664 X nodes、105,102 X edges、94 unique interactions、42 operator routes、42 identity decisions、84 operator-person root rows、42 actions。
- [x] 生成 CSV、JSON、GraphML、SQLite、README、method report 和 delivery index。
- [x] 运行 JSON/CSV/GraphML/SQLite、entity set、operator 双门槛/关系分层、预算 freshness、方向、证据、赞助语义与凭据卫生 QA；14/14 pass。
- [ ] Standalone v4 research workbook：**not produced**。所需 spreadsheet artifact runtime 在当时环境不可用；已交付可导入 spreadsheet 的 CSV。Cockpit campaign CSV/XLSX export 已在后续发布中实现并通过 disposable-DB E2E。
- [x] v4 historical validation：124/124 unit tests、Python compile、14/14 structural/semantic QA、independent read-only QA；最终 Cockpit release validation 见下方 2026-08-29 addendum。

## Decision gates and safeguards

- X 数据只走 Rapid X/cache；`secondary`/`third` 是 hop labels，不使用 LinkedIn 找关系。
- 部分 cache 的 absence 是 unknown；只有正向观察到的 edge 可以进入图谱。
- follow、reply、mention、quote 均不是 human relationship 或 intro willingness。
- funding 是 capacity/timing signal；除非有明确机制，不证明营销预算。
- spend freshness 使用 evidence-specific `spend_last_verified_at`；operator/profile 等无关刷新不能推进预算证据日期。
- public operator title 不证明 budget authority。
- Surf 余额不足时保留 `pending/unverified`，不拿旧知识冒充实时字段。
- live collection 必须 cache-first、cost-capped，并在扩 frontier 前先做 identity adjudication。

## Final handoff checklist

- [x] 机会池不再限于知名公司，且长尾与 crypto-AI 分 cohort 可审计。
- [x] spend mechanism、reachability、interaction、operator 与 intro willingness 分列。
- [x] 每条保留路径含中间人、hop 与 edge direction。
- [x] sponsorship 历史未被表达为当前 availability 或预算金额。
- [x] externally changing v4 claims 带 verification/freshness 字段或被显式标为 legacy/pending。
- [x] 未在导出文件或源码发现 active credential；`.env` 从未打印。
- [x] cache rebuild 与 live refresh 命令已记录。
- [ ] 若 Surf 恢复余额：刷新 17 家 crypto-AI 实时字段并重建评分。
- [ ] 由 Solomon/Mango 人工验证 connector 的真实关系、目标 operator 与 intro willingness。

## 2026-08-29 Solomon release completion addendum

- [x] 最终 Railway deployment `36550c7a-a88a-49af-a5a4-a9bc86e9bebd` 为 `SUCCESS`，实例 `RUNNING`；runtime image `sha256:335fb6ee2bc2ec018a50ce6aff40c2d40580245c52427869f15ffd1ace3d83e8`。
- [x] `/data` 启动前备份、preflight/runtime fail-closed contract 与单调迁移通过；生产为 89 companies / 29 operators / 640 intro-status rows / 42 actions / 64 sponsorship observations / 0 outreach / 0 shortlist。
- [x] 内部访问不使用密码页；`/healthz` 公开，未授权 root/API/OpenAPI 401，短期签名链接换取 HttpOnly cookie。
- [x] 四维语义、action-first 页面、person-dedup Network、Creator 分类、Campaign/Outreach 审计链路均已实现。
- [x] 全仓 468 passed + 57 subtests、0 failed；v4 QA 14/14；disposable E2E 126/126；interaction smoke 52/52；线上 UAT 97/97。
- [x] 最终 strict browser capture 为 13/13 current surfaces + 8/8 API probes；本域错误、截图/关闭错误、可见破图/loading 与内部断链均为 0。
- [x] 最终结论：**Ready with limitations**，可供 Solomon 内部 UAT 与辅助决策。
- [ ] 继续人工补齐 69/89 company freshness、29/29 operator field verification、64/64 sponsorship review、55/70 connector-pair checks，以及真实 intro/outreach/campaign outcomes。
- [ ] 收口 Railway platform manifest 配置漂移：当前 `healthcheckPath=null / retries=10`；Docker HEALTHCHECK 与 `/healthz` 已正常。

## 2026-08-30 Solomon v6 business-decision release

- [x] 用统一 canonical decision 取代冲突的首页分数、商业优先级与行动标签；Opportunity value 与 Execution readiness 分开。
- [x] 重构 Home、Opportunities、Company workspace、Route Portfolio、Creator commercial profile 与真实报价 Campaign preview。
- [x] 对 Priority 15 完成结构化 Asia review，并导入 ElevenLabs、Runway、Perplexity、PixVerse、Creatify 的可回查强信号。
- [x] employee routing 使用严格 employment evidence contract；本轮未找到合格路径，因此不显示空卡片，不将 X follow 当成 employment。
- [x] 保留 Runway 的弱二级 X 路径，但降级到 archive；官方 Partnerships 成为主路线。
- [x] Priority-15 英文事实在主决策层使用确定性中文，原文保留在折叠 archive，数字、日期、来源与 fact status 不变。
- [x] 全仓 468 passed + 57 subtests；disposable 1440×900 UAT 46/46；线上 public-only UAT 42/42；0 browser errors、0 生产写入。
- [x] 最终 Railway deployment `4ece90b4-dc10-4437-900d-aec3bb510fde` Online；preflight/runtime contracts 通过并生成启动前备份。
- [x] 发布报告：`kol_database/reports/SOLOMON_V6_RELEASE_REPORT.md`。
- [ ] 后续人工工作：复核公开 operator 当前职位与预算权限、审阅 65 条 sponsorship observations、复价历史 creator 报价、记录真实 outreach/campaign outcomes。
- [ ] 2026-12-01 前将 Railway Config-as-Code 从 `railway.json` 迁移到 `.railway/railway.ts`。

## 2026-08-30 Solomon v7 sales-execution release

- [x] 首页收敛为今日 Top 3：ElevenLabs、Gamma、Replit；PixVerse、Perplexity 放入独立候选队列。
- [x] 为 Top 5 建立 5 个 sales packets 与 25 条 buyer-map truth states，保留 economic buyer unknown，不用 operator 或团队入口冒充预算审批人。
- [x] 每家公司交付 Lean / Recommended / Premium 三档真实 creator mix；media cost、Mango fee、contingency 与 localization 分列。
- [x] 未配置 Mango service fee 时 fail visibly incomplete；内部保存会持久化 8 位 Recommended creators、rate-card 关联与 pricing assumptions。
- [x] 每家公司交付资格问题、switch rules、可复制 Email/X DM 和完整 Markdown 导出。
- [x] 本地 v7 UAT 全通过；生产 public-only UAT 全通过、0 browser errors、0 production writes attempted。
- [x] Railway deployment `ce74932c-8493-451b-bbda-b78079af9ab2` SUCCESS；启动前备份、单调迁移与 5 packets / 25 buyers runtime contract 通过。
- [x] 公开站点继续无需密码且只读；内部保存操作继续受 token/cookie 保护。
- [ ] 人工确认五家公司的 economic buyer、合同/采购流程、creator 最新档期与报价有效性。
- [ ] 对 Perplexity/Raman Malik、Replit/Tala Awwad 做首方当前任职复核；未通过前只走官方团队渠道。

## 2026-08-31 Solomon v8 campaign-trust release

- [x] Campaign recommendation 从通用 AI 类别匹配改为 company-specific capability profile；Runway 明确拆成 AI filmmaker、VFX/motion educator、creative director/studio、日本/亚洲创意节点和影视媒体/社区五类角色。
- [x] Runway Recommended 组合改为 Joshua Hoole、aiwithaly、Nijat Ibrahimli、Heather Cooper；通用 AI X/KOC 账号不再因为“AI + 有报价”自动进入。
- [x] 历史报价只有明确覆盖建议交付范围才计入小计；普通 X post、quote repost、IG Reel 或通用视频报价不能冒充 film production、workshop 或 creative direction 报价。无覆盖报价时显示“待逐一询价”，不显示 `$0` 方案价。
- [x] Lean / Recommended / Premium 通过目标、角色覆盖和未计价项目级 scope 形成战略差异；所有档位明确标记 program budget incomplete，直到 Mango fee、制作费、场地/放映等输入齐全。
- [x] Network 增加条件渲染的 Verified person-to-person paths；只保留终点为真实员工、逐跳方向完整且至少有互关/互动/共同经历证据的路径，终点是否买方单独标记。
- [x] Creator 商业情报 disclaimer 提升到区块级；Daniel 的缓存/实时状态、NULL likes、推广关键词命中率与 Gamma 两条待审核观察已按真实语义展示。
- [x] 以 exact display name + exact public business email 合并 Jan Mraz 7 个跨平台记录，旧 ID 通过 alias 可解析；Needs Review 从 33 降至 8，保留真正不确定实体。
- [x] Opportunities 默认表收敛为 7 列；“Mango 可卖”移入行内展开摘要，移除屏幕阅读器重复隐藏长文，并把核心判断句统一成中文。
- [x] 访问边界改为 fail-closed：GET/HEAD/OPTIONS 公开；未配置 token 的写请求返回 503，已配置但未授权返回 401，避免部署漏配时写入意外公开。
- [x] 本地验收：Cockpit 351 passed + 50 subtests；legacy 125 tests OK；前端语法与 diff whitespace 检查通过；真实 HTTP 边界 `GET 200 / no-token POST 503 / unauthenticated configured POST 401`。
- [x] Railway v8 deployment `ccc2202c-4317-4ddc-a22b-f54b3f8e0562` SUCCESS；production 首页与关键 GET 均为 200，访问状态确认 token 已配置且匿名只读，匿名无效 POST 返回 401、无写入。
- [ ] 视觉 UAT：当前 Codex 内置浏览器没有可用 browser instance；部署后需由 Solomon 用普通桌面和移动浏览器人工检查 Runway、Network、Daniel 与 Opportunities 四个页面。
- [x] Network 中文交付清理 deployment `44442367-e07b-422f-bfbf-2fbbbe5fffbb` 已在线；正式渠道与合作项目的动态 why/first-step 完整英文判断映射缺口降至 0，三条已报告的中英文拼接均已在线修复。

## 2026-09-01 Apify research and Gamma company-specific expansion

- [x] 接入 `APIFY_TOKEN`，实现 Bearer-header 客户端、Actor/build 固定、单次费用上限、幂等 GET retry、raw cache、run manifest 与人工复核候选队列；token 不进入 URL、日志、缓存或前端。
- [x] 按“官网首方 → LinkedIn 当前职位候选 → YouTube sponsorship → 公开路线 → campaign/creator 扩展”顺序完成 Gamma 第一批 8 次有上限采集；最终费用 USD 0.524701，8/8 succeeded。
- [x] 官网得到 4 个有效首方页面；其中 Japan Regional Community 职位明确提到本地 creator、workshop、localized learning、Gamma 101 与 community activation。一个 API/Ecosystem 职位 URL 返回 404，保留审计但不进入证据候选。
- [x] LinkedIn 20 条原始员工结果中 10 位通过“当前职位在 Gamma + 相关职能”候选门槛；写入数据库的 9 位新人全部保持 identity unconfirmed，1 位已有记录保留。
- [x] 两轮 YouTube 各 30 条：形成 9 条品牌归因商业候选、6 条归因待定；新增 6 位 creator 全部进入 Needs Review，新增 6 条 sponsorship 全部为 unreviewed。
- [x] 找到 21 个日语 Gamma 内容候选并形成 8 人 Japan shortlist；内容语言与 creator 所在地/受众地域严格分开，未自动描述为日本 KOL 或已愿意合作。
- [x] 公开联系方式 Actor 本轮返回 0 条，不表述为“没有邮箱”；另外通过官方页面确认 Affiliate PartnerStack 申请和 Gamma Community 两条公开路线。
- [x] Gamma Company detail 新增候选研究层，按官网、operator、公开路线、全球历史合作 cohort 和 Japan discovery cohort 分层展示。
- [x] Gamma Campaign Preview 不再复用通用 AI/productivity 名单；Recommended 改为 Daniel、Jeff Su、Tiago Forte、Tool Finder、Kevin Stratvert、One Skill PPT，并逐人展示公司特定依据和未复核风险。
- [x] 三档只显示交付范围匹配的已知报价：当前均为 Daniel USD 1,200 creator-media 小计；其余 creator、Mango fee、本地化、workshop 与日本层均显式未计价，`program_budget_complete=false`。
- [x] 定向验收：Apify 18 tests + Cockpit 84 tests 通过；最终 deploy seed contract 为 97 companies / 53 operators / 71 sponsorship observations；公开 API allow-list 未暴露 token、raw cache、run 或 dataset metadata。
- [x] 第二批扩展到 sponsorship 证据最薄的 Tavus 与 Hedra：各完成官网、LinkedIn 和 YouTube 三路采集；项目 run index 现为 14/14 succeeded，总费用 USD 1.088169。
- [x] Tavus：3 个有效官网页面、9 位 operator 候选、20 条 YouTube observations；只有 1 条商业归因待定、0 条品牌归因候选，因此不生成 creator cohort。
- [x] Hedra：2 个有效官网页面（另 1 页无效保留审计）、2 位 operator 候选、20 条 YouTube observations；4 条 affiliate 线索全部归因待定，0 条品牌归因候选，因此不生成 creator cohort。
- [x] 第二批新增 10 位 operator，全部保持 identity unconfirmed；总 deploy seed 为 97 companies / 53 operators / 71 sponsorship observations。
- [ ] 人工复核 10 位 operator 当前身份、15 条商业候选、8 位 Japan shortlist 的受众地域/报价/商业意愿，再决定哪些可转为 confirmed 或进入真实外联。
- [ ] 视觉 UAT 未完成：当前 Codex 浏览器环境没有任何可用 browser instance；需要在普通桌面/移动浏览器查看 Gamma Company detail 与 Campaign Preview。
- [ ] 本轮改动尚未发布到 Railway；上线前应先完成视觉 UAT，再按现有只读公开/写操作保护边界部署。

## 2026-09-01 Priority 15 horizontal BD refocus

- [x] 冻结 Gamma 深挖，并停止为 Tavus/Hedra 等品牌归因不足的公司扩张 creator cohort；新增横向批次只跑首方官网与 LinkedIn 当前职位候选。
- [x] Priority 15 全部完成首方页面与 operator candidate 横向覆盖；公开包每家公司只保留最相关的 1–3 人，LinkedIn 不用于关系路径，也不证明身份、预算权或回复意愿。
- [x] 建立 `priority_company_bd_cards_v1.json`，逐家公司按“需求信号 → 负责人 → 进入路线 → 联系节点 → 下一步”输出，并保留事实、推断、待复核与关键未知项边界。
- [x] Perplexity、Wispr Flow 与 Gamma 的首方路线/亚洲判断写入 canonical decision；本地 runtime 与 deploy seed 同步为 97 companies / 89 operators / 15 decisions / 15 Asia profiles。
- [x] 人脉网络增加跨公司联系情报：6 个 creator 商业流程节点、9 个 X 多目标研究节点；不把历史合作或 X 可达性写成 warm intro。
- [x] Gamma 三档价格在金额相同且项目成本不完整时降级为 `concept_only_pricing_incomplete`；前端只显示“已有活动概念，但报价未完整”。
- [x] Apify run index 刷新为 39/39 succeeded、344 raw observations、总费用 USD 2.636794；公开 API 不暴露 Actor input、raw cache、run/dataset ID 或 credential。
- [x] 定向验收：Apify 21/21；Campaign/decision/runtime 60/60；Priority 15 API 15/15 首方与 operator 覆盖；seed/runtime contract、JS syntax、Python compile 全部通过。
- [ ] 人工复核 15 家 operator shortlist、官方 route 当前有效性与实际预算 owner；完成 Gamma/Network/Company detail 的桌面和移动视觉 UAT。
- [x] Railway 最终部署 `20f0a420-fb21-4c03-b432-eef967389db0` 已成功上线；旧持久库迁移到 97 companies / 77 operators / 71 sponsorship observations / 8 Asia evidence，第二次启动 0 新增。
- [x] 线上 public read-only、匿名写入 401、首页决策层、Gamma 详情/销售包/活动预览、人脉网络和创作者 API 均通过；线上 `app.js` 与本地发布文件哈希一致。
- [x] 可见个人名标题、路径根和客户邮件签名已改为团队/内部角色表述；客户邮件统一由 `Mango Labs BD Team` 签名。
- [ ] 点击式视觉 UAT 仍待完成：当前应用内 Browser 列表为空。连接 Browser 后逐页检查首页、商机、人脉网络、创作者、Gamma 详情和活动预览的桌面/移动视图。
