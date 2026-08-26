# Mango Labs BD Intelligence

Mango Labs 的证据化增长研究与 BD intelligence 系统。当前 v4 快照日期为 `2026-08-25`，把公司发现、预算/投放机制、operator 路由、creator sponsorship、Solomon/Mango 的 X 关系路径及可执行行动合并为同一套可复跑数据产品。

## 当前交付

- **77 家公司**：26 家 v3 保留目标、34 家长尾 AI、17 家 crypto-AI。
- **42 条行动**：30 条新增目标、5 条保留 v3 路径、7 条额外 v3 Mango-seed 路径；Wave 1/2 各 21 条。
- **Solomon 新目标图谱**：51 家中 48 家 X 身份严格确认；22 家可达，其中 1 家 direct、21 家 secondary；另保留 440 条 secondary/third alternatives。
- **Mango seed 图谱**：20 个 seed accounts，74 条 secondary paths，覆盖 28 家公司；Mango 官方账号到目标公司的 direct paths 为 0。
- **公开互动证据**：Solomon 路径范围 38 条，Mango 路径范围 58 条；合并后 94 条唯一互动。公开 reply/mention/quote 不代表真实关系或引荐意愿。
- **Operator remediation**：42/42 action companies 已完成公开研究；严格通过官网/公司当前职位证据 + Rapid X exact profile 双门槛的 named operator 为 10 家（全量 6/77→10/77；queue 10/42），其余 32 家都有具体 acquisition path。
- **Person-level reachability**：Solomon→operator person 可达 6 家，Mango→operator person 可达 5 家；company-account path 与具体人 path 独立存储，均不等于可引荐。
- **Legacy audit fixes**：26 条 legacy 裸高置信预算标签已全部改为 `legacy_unrevalidated_*`（冲突 26→0）；Replit、Cursor、Runway、Perplexity、Pika 的路径方向已回填；QA 已加入基于 `spend_last_verified_at` 的 high-confidence freshness/revalidation gate，operator 等无关刷新不能掩盖过期 spend 证据。
- **Sponsor intelligence**：52 条 atomic YouTube observations、14 个 brands、41 个 creators、50 条 creator-brand edges。
- **统一业务图谱**：527 nodes、1,101 条 typed directed edges、688 条 retained paths；公司账号、operator person、public business route、creator/sponsor 与 GTM 层可联查。完整技术 X 图仍含 73,664 nodes / 105,102 条 directed follow edges。
- **结构化查询层**：SQLite 加入 42 operator routes、42 operator identities、84 Solomon/Mango operator-person path status rows 和 `v_operator_relationships`，并保留 77 家公司、94 条唯一互动与 42 条行动。
- **QA**：14/14 checks、124/124 unit tests 与 Python compile 全部通过；独立只读审计完成最终收口。Surf 因账户返回 `PAID_BALANCE_ZERO`，本快照有 0 个 Surf-verified fields。

知名度、粉丝数、融资额、token value 与 TVL 不直接加分。v3 与 v4 使用不同评分方法，只能在各自 comparison group 内排序；系统不制造 1–77 的虚假全局数值排名。

## 从哪里开始

- `outputs/pilot_v4/DELIVERY_INDEX.md`：交付目录、关键数字、构建顺序与限制。
- `outputs/pilot_v4/company_master_v4.csv|json`：77 家公司的 operator-facing 单表视图。
- `outputs/pilot_v4/priority_action_queue_v4.csv|json`：42 条行动，含 owner、路径、开场、fallback、验证问题与成功条件。
- `outputs/pilot_v4/operator_x_identity_resolutions_v4.json`：42 家的严格 operator identity gate 结果。
- `outputs/pilot_v4/operator_person_paths_v4.json`：Solomon/Mango 到严格确认 operator 个人 X account 的独立路径层。
- `outputs/pilot_v4/METHOD_AND_EXPANSION_REPORT.md`：中文方法、发现、Solomon 视角和建议运营方式。
- `outputs/pilot_v4/business_knowledge_graph_v4.json|graphml`：跨公司、X、人物、creator、sponsor、operator 与 GTM 的统一图谱。
- `outputs/pilot_v4/mango_bd_v4.sqlite`：带外键、索引与查询视图的数据库。
- `outputs/pilot_v4/QA_REPORT_v4.md|json`：最终结构与语义 QA。

## 关系图谱语义

`secondary` / `third` 只是类似 LinkedIn 的度数命名，不表示使用 LinkedIn。所有 X 身份、followers、following 和互动均来自 Rapid X 及其本地 cache。

- direct：1 hop；secondary：2 hops；third：3 hops。
- following snapshot：`observer → member`；followers snapshot：`member → observer`。
- mutual 只有在两个方向都被正向观察时才成立。
- shared-interest V-shaped pattern 不是可路由 intro path，已拒绝 19,343 条此类候选。
- 部分 cache 中的缺失是 unknown，不是 negative edge。
- graph reachability、公开互动、person-level relationship 和 willingness to introduce 是四个独立状态。

Solomon root 的 following/followers 均完整：1,731 following、3,936 followers、5,058 unique first-degree accounts、609 mutuals。扩展层使用 20 个优先 connectors；following 20/20 完整，followers 11/20 完整、9/20 page-capped partial。新增目标共枚举 34,707 条 routable paths，主路径保留 22 条，alternatives 保留 440 条，其余 34,245 条因每目标上限被截断。

Mango 审计包含官方账号和 19 个 seed accounts。following 20/20 完整，followers 12/20 完整、8/20 partial；全 77 家目标中 66 家有精确技术身份、11 家未解析。系统没有从 seed 名单推断 employment，也没有确认任何 human introduction。

关键关系 artifacts：

- `outputs/pilot_v4/x_graph_summary_v4.json`
- `outputs/pilot_v4/solomon_paths_expanded_v4.csv|json`
- `outputs/pilot_v4/solomon_graph_expanded_v4.graphml`
- `outputs/pilot_v4/x_path_interactions_v4.csv|json`
- `outputs/pilot_v4/mango_seed_network_audit.csv|json`
- `outputs/pilot_v4/mango_seed_target_paths.csv|json`
- `outputs/pilot_v4/mango_path_interactions_v4.csv|json`

## 公司、spend 与 action 逻辑

51 家新增目标的 spend evidence 分布：L1 6 家、L2 17 家、L3 28 家。

- L1：capacity / financing signal only。
- L2：formal program、in-kind、co-sell、channel 或计划中的 revenue share。
- L3：explicit cash、commission 或 active creator/partner spend mechanism。

这仍然是机制证据，不等于当前可用预算。`budget_authority_confirmed` 在当前数据中全部为 false，需要 outreach 验证。

v4 权重：reachability 30%、spend mechanism 25%、timing 15%、Mango fit 15%、buyer clarity 10%、evidence quality 5%。新增目标 graph-adjusted top opportunities 包括 PixVerse、Gumloop、Vapi、Meshy、OpenArt、Talus Network、MyShell、Tavus、PIN AI、Allora Network 和 Kite AI；应以 action queue 的具体 opener、route 与验证问题执行，而不是只看公司名次。

## Sponsor、operator 与 GTM

Kartr 只作为流程 benchmark。Mango 的实现保留 atomic source、日期、分类与 provenance，并严格区分 paid sponsorship、affiliate 与 ordinary mention：

- 36 条 explicit paid disclosures、7 条 affiliate、9 条 mention/negative controls；
- 50 条 creator-brand edges，其中 2 条 repeat-paid、8 个 multi-creator-paid brands；
- 5 个 paid-only cross-brand creator bridges；
- affiliate 或 mention 永远不会被提升为 paid。

`data/pilot_v4/top_operator_routes.json` 覆盖 action queue 全部 42 家；10 家有严格确认的 named operator，32 家没有写入猜测姓名、而是保留 `acquisition_required_via_*` 的具体公开获取路径。严格确认要求官网/公司控制来源能证明当前职位，且 Rapid X exact profile 通过确定性姓名、handle 和公司关联 gate；LinkedIn 不在此流程内。公开姓名/职位仍不代表预算审批权。

`company_account_relationship` 与 `operator_person_relationship` 是两个独立事实：前者终止于公司 X account，后者只终止于通过严格门槛的个人 X account。当前 Solomon/Mango 到具体 operator 的技术可达数分别为 6/5，intro willingness 仍全部未验证。`data/pilot_v4/gtm_case_studies_v4.json` 含 8 个成熟公司 GTM cases，作为渠道/机制参考，不混入目标公司评分。

## 从缓存重建

以下命令不产生网络调用：

```bash
PYTHONPATH=src python3 scripts/normalize_v4_evidence_contract.py --check
PYTHONPATH=src python3 scripts/build_v4_candidates.py
PYTHONPATH=src python3 scripts/build_v4_x_graph.py
PYTHONPATH=src python3 scripts/build_mango_seed_audit.py
PYTHONPATH=src python3 scripts/build_sponsor_intelligence_expanded_v4.py
PYTHONPATH=src python3 scripts/build_operator_person_paths_v4.py
PYTHONPATH=src python3 scripts/build_operator_routes_v4.py
PYTHONPATH=src python3 scripts/build_priority_action_queue_v4.py
PYTHONPATH=src python3 scripts/build_company_master_v4.py
PYTHONPATH=src python3 scripts/build_business_knowledge_graph_v4.py
PYTHONPATH=src python3 scripts/build_v4_database.py
PYTHONPATH=src python3 -m unittest discover -s tests -v
PYTHONPATH=src python3 scripts/run_v4_qa.py
```

若要刷新 operator research/identity，准确的 downstream 顺序是：

```bash
PYTHONPATH=src python3 scripts/merge_operator_research_v4.py --wave1 /path/to/operator_wave1.json --wave2 /path/to/operator_wave2.json
PYTHONPATH=src python3 scripts/collect_operator_x_identities_v4.py --env-file .env
PYTHONPATH=src python3 scripts/build_operator_person_paths_v4.py
PYTHONPATH=src python3 scripts/build_operator_routes_v4.py
PYTHONPATH=src python3 scripts/build_priority_action_queue_v4.py
PYTHONPATH=src python3 scripts/build_company_master_v4.py
PYTHONPATH=src python3 scripts/build_business_knowledge_graph_v4.py
PYTHONPATH=src python3 scripts/build_v4_database.py
PYTHONPATH=src python3 scripts/run_v4_qa.py
```

Operator 流水线采用两阶段 bootstrap：merge/routes builders 先读取现有 `priority_action_queue_v4.json` 固定 42 家集合与 wave，action builder 再读取新 routes 并把严格 operator fields overlay 回 canonical queue。第一次初始化必须先准备同一 42 家集合的 bootstrap queue；公司集合变更时必须同步 bootstrap queue 与 research packets，不能混用旧 routes。Identity collector 为 cache-first，但缺 cache 时会访问 Rapid X；`--force` 仅在确认配额且确实需要刷新时使用。

## 刷新 live X evidence

凭据只放在 ignored、权限为 `600` 的 `.env`。不得把 key 写入 `.env.example`、源码、报告、导出表或 cache payload。

```bash
PYTHONPATH=src python3 scripts/build_v4_candidates.py
PYTHONPATH=src python3 scripts/collect_solomon_x_network.py --scope solomon-followers --env-file .env
PYTHONPATH=src python3 scripts/collect_mango_seed_network.py --stage all --env-file .env --following-pages 5 --follower-pages 1
PYTHONPATH=src python3 scripts/expand_solomon_frontier.py --stage all --env-file .env --top-connectors 20 --connector-following-pages 3 --connector-follower-pages 1
PYTHONPATH=src python3 scripts/collect_x_identity_corrections.py --env-file .env
PYTHONPATH=src python3 scripts/build_v4_x_graph.py
PYTHONPATH=src python3 scripts/build_mango_seed_audit.py
PYTHONPATH=src python3 scripts/collect_x_interactions_v4.py --env-file .env --count 20
PYTHONPATH=src python3 scripts/collect_mango_path_interactions_v4.py --env-file .env --count 20
PYTHONPATH=src python3 scripts/collect_operator_x_identities_v4.py --env-file .env
```

采集是 cache-first、cost-capped。先解析身份和重排 frontier，再扩大 connectors 或 page limits；互动采集必须在新 graph/path build 之后执行。Operator refresh 后按 `person paths → routes → action queue → company master → business graph → SQLite → QA` 重建。不要用 `.env.example` 覆盖已有 `.env`；分段 seed collection 产生的子集 profile 文件也不能直接作为最终全量 audit 输入。

## 数据契约与治理

每个 material claim 应保留 source URL 或 local evidence ID、observed/published date、`last_verified_at`、evidence type、confidence 与 fact status。stable ID 与 display name/aliases 分离，所有关系边保留方向和 provenance。

- `AGENTS.md`：持久运行规则、数据源路由、证据契约和图语义。
- `PLAN.md`：本轮 goal、完成状态、阻塞项与 handoff。
- `.env.example`：只保留空变量名。

当前没有生成 workbook：本次运行环境缺少要求的 spreadsheet artifact runtime，因此交付 CSV、JSON、GraphML 与 SQLite；没有用未授权的替代库伪造 workbook。旧 v1–v3 产物保留在 `outputs/pilot*` 供审计，当前决策以 v4 为准。
