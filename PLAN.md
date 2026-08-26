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
- [ ] Workbook：**not produced**。所需 spreadsheet artifact runtime 在当前环境不可用；已交付可导入 spreadsheet 的 CSV，不使用未授权替代 runtime。
- [x] Final full validation：124/124 unit tests pass、Python compile pass、14/14 structural/semantic QA pass、independent read-only QA pass。

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
