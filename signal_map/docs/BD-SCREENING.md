# 可 BD 筛选 · 接口契约

内部作业面。**全部路由在 `/api/internal/bd` 下，需要 `SIGNAL_MAP_INTERNAL_TOKEN`。**
这里返回成本、联系方式、供应商身份和内部备注，客户接口（`/api/client/*`）不引用本层任何东西。

```
Authorization: Bearer $SIGNAL_MAP_INTERNAL_TOKEN
```

---

## 方法：先定目标人物，再从他们关注的账号里找人

客户选的目标人物是**名人** —— Elon Musk、Sam Altman、a16z 与 Sequoia 的 GP、
Karpathy、LeCun。**这些人一个都买不到**：他们是客户希望被看见的受众。能买的是
**他们关注的那批账号**。

所以候选池的第一来源是 `follow_edges` 里 `目标人物 → 关注 → 账号` 这个方向，
由 [collect_target_following.py](../scripts/collect_target_following.py) 采集。
这条边同时是**发现依据**和**关系证据**。

与之对照，`roster_centrality`（我们自己的创作者关注了谁）只是补充面。选了目标
人物时它默认关闭 —— 它反映的是 Mango 签了谁，实测中会把同圈互推账号排到前面。
两者在 `discovery_basis.source` 里用不同的值区分，任何界面都不得把后者显示成
「目标人物关注了他」。

---

## `POST /api/internal/bd/screen`

一次调用返回两块，两块共用同一组偏好，随偏好实时变化。

### 请求

```jsonc
{
  "verticals":       ["ai"],                    // 领域
  "market_regions":  ["europe_america"],        // 市场（本产品只做海外）
  "languages":       ["en"],
  "audience_types":  ["developers", "founders"],// 圈层
  "platforms":       ["X", "YouTube"],
  "target_handles":  ["karpathy", "sama", "pmarca"],  // 具体目标人物

  "total_budget_usd": 30000,                    // 只影响「有报价」那一块
  "objectives":       ["credibility"],
  "content_formats":  ["x_thread"],

  "min_targets": 2,          // 至少被几位目标关注才进池子（默认 2 = 共同关注）
  "priorities":  [],         // 只看某几档优先级，空=全部
  "include_roster_centrality": false,
  "limit": 60, "priced_limit": 30
}
```

每一项都可以不填。**不填 = 没问**，对应的判断轴返回 `not_asked`，不会被当成
`unknown` 拉低任何东西。

### 响应骨架

```jsonc
{
  "preferences": { ... },              // 原样回显，前端可据此判断结果是否过期
  "computed_at": "2026-09-09T...",
  "rule_versions": { "recommendation": "...", "bd_screening": "..." },

  "priced_recommendations": {          // 已有报价 → 现在就能报给客户
    "pool_size": 276, "returned": 30,
    "items": [ /* 13 轴评估，见下 */ ],
    "supply_gaps": [ /* 库存里根本没有的东西，明说 */ ]
  },

  "bd_candidates": {                   // 无报价 → 需要 Mango 建联
    "pool_size": 1603, "matched": 428, "returned": 60,
    "by_priority":    { "priority_inquiry": {"count": 28, "label_zh": "优先询价"}, ... },
    "by_source_kind": { "creator": {...}, "observed": {...}, "ingested": {...} },
    "items": [ /* 候选卡片，见下 */ ]
  },

  "collection_gaps": [ /* 还缺哪些采集 */ ],
  "reading_note": "..."
}
```

---

## 候选卡片：一张卡要能回答的八件事

规格要求每个候选至少能看清：为什么适合、关联哪些目标、商业合作证据、联系方式、
当前报价与跟进状态、优先级、缺失信息、下一步。对应字段：

| 问题 | 字段 |
|---|---|
| 为什么适合 | `domain_fit.axes[]`（七条逐轴结论） + `domain_fit.summary` |
| 关联哪些目标 | `relation_evidence.targets[]`（每条都带 `direction_text`） |
| 商业合作证据 | `commercial_maturity.stances` + `.signals[]`（每条带出处和核验时间） |
| 联系方式 | `commercial_maturity.signals[]` 中 `source == "contact_methods"` 的行 |
| 当前报价 | `quote_state.quotes[]` + `quote_state.in_client_pool` |
| 跟进状态 | `bd_status` / `bd_status_note` / `owner` / `last_contact_at` |
| 优先级 | `priority` / `priority_label_zh` / `priority_reason` |
| 缺失信息 | `evidence_completeness.missing[]` |
| 下一步 | `next_step_kind` / `next_step` |

### 四份判断分开展示，任何一项都不能抵消另一项

这是硬要求，不是建议。界面上必须是四块，不能合成一个分数：

```jsonc
{
  "domain_fit": {
    "verdict": "fit",            // fit | partial | mismatch | unclear
    "label_zh": "领域适配",
    "summary": "符合 内容与行业、市场、平台",
    "axes": [
      { "key": "content_vertical", "label_zh": "内容与行业", "verdict": "match",
        "detail": "内容领域覆盖 ai", "evidence": "依据：公开简介（全部已归一领域：ai）" },
      { "key": "market_region",  "verdict": "match",   "detail": "市场为欧美", ... },
      { "key": "language",       "verdict": "unknown", "detail": "内容语言未知" },
      { "key": "platform",       "verdict": "match",   ... },
      { "key": "audience",       "verdict": "match",   ... },
      { "key": "activity",       "verdict": "unknown", "detail": "观察层仅抓取过一次公开资料..." },
      { "key": "originality",    "verdict": "unknown", ... },
      { "key": "engagement",     "verdict": "unknown", ... }
    ]
  },

  "relation_evidence": {
    "distinct_targets": 2,
    "targets": [
      { "target_handle": "karpathy",
        "direction_text": "@karpathy 关注了本候选",   // 方向必须显示
        "signal_type": "follow", "direction": "source_to_target",
        "occurred_at": null,                          // X 不暴露关注开始时间
        "collected_at": "2026-09-09T...",
        "confidence": "research_lead",
        "target_review_status": "pending",            // 目标本人是否已人工确认
        "target_root_type": null,
        "evidence_url": "https://x.com/karpathy/following" }
    ],
    "priority_signals": ["2 位目标人物共同关注"],
    "coverage_note": "所选 3 位目标人物中，2 位的公开关注列表已采集；其余 1 位未采集...",
    "claim_limit": "关注只证明观察时存在关注关系，不证明看过、认可或会转发；零结果表示当前未观察到，不表示两者无关系"
  },

  "commercial_maturity": {
    "level": "proven",                        // none|contact_only|stated|proven|confirmed
    "level_label_zh": "有第三方合作案例",
    "stances": {                              // 三件事分开，永不合并
      "contactable":            { "holds": true,  "label_zh": "有可执行联系路径", "evidence_count": 2 },
      "has_commercial_history": { "holds": true,  "label_zh": "过去承接过第三方合作", "evidence_count": 4 },
      "confirmed_willing":      { "holds": false, "label_zh": "已确认愿意承接本项目" }
    },
    "signals": [
      { "signal_type": "business_email", "value": "hi@example.com",
        "evidence_quote": "…Business: hi@example.com…",   // 原文，必须真的出现过
        "verified_at": null, "note": "未核验：尚无人确认该联系方式确实可达" },
      { "signal_type": "third_party_sponsorship", "value": "Gamma",
        "review_status": "unreviewed", "note": "付费观察·待复核" }
    ],
    "caveats": ["简介出现换量/互推/代写类说法，是在售卖曝光位，不等于承接第三方品牌投放"],
    "blocking": []
  },

  "evidence_completeness": {
    "coverage_text": "6/10",
    "known":   ["内容领域", "市场归属", "粉丝规模", ...],
    "missing": ["近期活跃度", "播放/互动数据", "报价", "目标关注关系"]
  }
}
```

### 三件事的强度是递增的，不能互相顶替

| 事实 | 由什么产生 | 不代表什么 |
|---|---|---|
| `contactable` 有邮箱/表单/已确认代理 | 简介里字面读到、外部名单、`contact_methods` | 不代表他接第三方投放 |
| `has_commercial_history` 接过第三方广告 | `sponsorship_evidence`、`third_party_sponsorship` | 不代表他现在还接、愿意接这个项目 |
| `confirmed_willing` 确认愿意接本项目 | **只能**来自真实回复，写入必须 `verified: true` + `actor` | — |

`self_promotion_only`（推广自有课程/社群、"DM for collabs" 这类换量说法）
**记录并展示，但不支持上面任何一件**。这一条实测中拦下过一批误判：只按措辞收，
「优先询价」前八名全是同圈互推账号。

### 四档优先级的判定顺序

```
明确拒绝              → 仅作目标或观察
标记为目标人物         → 仅作目标或观察（关系强度不替代商业意愿）
标记为供应商           → 先确认合作意愿（确认代理范围，本身不投放）
非创作者账号           → 仅作目标或观察
领域不符               → 仅作目标或观察   ← 联系方式不能抵消内容不相关
适配无法判断           → 仅作目标或观察 + 补充样例
可联系 且 有付费证据    → 优先询价
可联系                → 先确认合作意愿
其余                  → 优先补充商务路径   ← 没有联系方式不删人
```

`bd_candidates.items` 的顺序是**建联队列顺序**，不是匹配度排名。没有项目简报时
不生成统一的项目匹配排名，所以卡片里**没有任何 0–100 的匹配分**。

---

## 已有报价那一块

走既有的 13 轴引擎，偏好被包装成一份**不落库**的 Brief —— 试筛不会在客户的项目
历史里留下简报，也不会产生 `RecommendationRun`。

```jsonc
{ "creator_id": 58, "creator_name": "kimmonismus",
  "axes": [ { "key": "attention_path", "verdict": "partial",
              "detail": "被候选账号（尚未人工确认为 Root） @garrytan、@levelsio 关注（关注信号，非互动证据）" }, ... ],
  "matched": [...], "unmet": [...], "missing": [...],
  "reasons": [...], "risks": [...], "next_steps": [...],
  "quote": { "internal_cost_usd": 1300, "client_price_band": "$1,000–2,500", "client_visible": true },
  "rank_score": 0.71 }
```

`rank_score` **仅用于排序，任何界面都不得单独展示**。

`supply_gaps` 报的是「库存里根本没有符合这个条件的人」，例如：

```jsonc
[{ "axis": "market_language", "label_zh": "市场与语言",
   "detail": "已有报价的 276 个对象中，没有一个符合所选「市场与语言」条件",
   "next_step": "这是供给缺口，需要新建联，而不是从现有名单里挑一个近似的" }]
```

按设计 `mismatch` 不淘汰候选，所以界面仍会列出人。**没有这一段，看的人会以为
那就是推荐结果。**

---

## 写操作

| 方法 | 路径 | 作用 |
|---|---|---|
| `GET`   | `/options` | 全部词表 + 可选目标人物（已确认 Root / 未确认候选**分开返回**） |
| `GET`   | `/candidates/{platform}/{handle}` | 单个候选，偏好走 query string（详情页要能分享成链接） |
| `PATCH` | `/candidates/{platform}/{handle}` | 跟进状态、负责人、身份判定、下一步 |
| `POST`  | `/candidates/{platform}/{handle}/commercial-signals` | 记录一条商业证据 |
| `POST`  | `/candidates/{platform}/{handle}/contacts` | 记录联系方式（带来源与核验） |
| `POST`  | `/candidates/{platform}/{handle}/quotes` | **取得报价 → 进入客户筛选池** |
| `POST`  | `/candidates/{platform}/{handle}/tasks` | 把下一步落成内部工单 |

所有写操作都要 `actor`。两条会返回 422 的硬约束：

- 商业证据必须带 `evidence_url` 或 `evidence_quote`。没有出处的证据不是证据。
- `confirmed_willing_reply` 必须 `verified: true`。这句话只能来自真实回复。

### 报价写回

```jsonc
POST /candidates/X/somekol/quotes
{ "actor": "fiona", "raw_text": "Thread $800, video $2000",
  "amount": 800, "currency": "USD", "platform": "X", "content_format": "x_thread",
  "quote_source": "kol_direct", "source_detail": "经 Smooth Media",
  "quoted_at": "2026-09-09", "valid_until": "2026-12-31" }
```

- **原文必存**（`raw_text`），解析器还会改进，改进后要能重新解析。
- **追加，不覆盖**：同一个人可以有多轮报价，历史一条不删。
- 金额写进 `internal_cost_usd`（Mango 的成本），**不写** `client_price_usd`。
  抄过去会同时泄露成本并抹掉利润。对客价由人在
  `PATCH /api/internal/quotes/{id}/pricing` 另行确定。
- 写完之后这个人自动进入客户筛选池（`quote_state.in_client_pool == true`），
  不需要额外搬运。

---

## 外部名单：待核验输入，不参与判断

老板给的 sample、建联工作表由
[ingest_bd_candidates.py](../scripts/ingest_bd_candidates.py) 入库。它们**不构成
候选池** —— 池子每次现算。名单只做两件事：接到已有身份记录上（去重），以及把
自己的说法原样存下来。

```jsonc
"source_claims": {
  "source_name": "ilands_html", "source_ref": "ilandsExpandedKolPool[0]",
  "score_raw": "96.03", "tier_raw": "A_paid_reachable",
  "notes_raw": "lane=consumer_social_ai；ilands_fit=medium；...",
  "status": "unverified",
  "warning": "来源名单的评分与分层为待核验输入，未参与本页任何判断"
}
```

`ilands_fit` 是**那一个客户的偏好**，不是通用适配度。把它读成产品逻辑，就是把
一个客户的口味写死进所有客户的筛选。

---

## 采集缺口会如实报出来

```jsonc
"collection_gaps": [
  { "kind": "target_not_collected",
    "detail": "N 位所选目标人物尚未收录到 X 账号库",
    "next_step": "采集这些账号的公开资料与关注列表" },
  { "kind": "following_list_not_collected",
    "detail": "N 位已收录目标人物的公开关注列表尚未采集",
    "next_step": "对这些目标人物运行 collect_target_following" }
]
```

**零结果 = 当前未观察到，不是两者无关系。** 被页数截断的关注列表里「没有某条边」
同样什么都不能说明，`relation_evidence.limitations[]` 会带出来。

---

## 采集

按这个顺序跑，每一步都依赖上一步的产物：

```bash
# 0. 建表 / 补列（幂等）
.venv/bin/python -m signal_map.scripts.add_bd_candidate_tables

# 1. 外部名单入库 —— 目标人物由此进来（object_kind=target_person）
.venv/bin/python -m signal_map.scripts.ingest_bd_candidates --all --dry-run
.venv/bin/python -m signal_map.scripts.ingest_bd_candidates --all

# 2. 采目标人物关注了谁（方法主线）。62 位约 150k 条边。
.venv/bin/python -m signal_map.scripts.collect_target_following --from-candidates
.venv/bin/python -m signal_map.scripts.collect_target_following --handles @karpathy,@sama

# 3. 给被共同关注的账号补公开资料。没有简介就判不出内容和商业信号，
#    这一步不做，队列里全是只有 rest_id 的空壳。批量端点，很便宜。
.venv/bin/python -m signal_map.scripts.enrich_cofollowed_profiles --min-targets 5

# 4. 落一份实际筛选结果（和接口同一套判断代码）
.venv/bin/python -m signal_map.scripts.report_bd_screening \
    --verticals ai --platforms X --targets-from-candidates --min-targets 5

.venv/bin/python -m pytest signal_map/tests/test_bd_candidates.py -q
```

### 2026-09-09 首次实跑

62 位 iLands 目标人物 · 领域 ai · 平台 X · 共同关注门槛 5：

| | |
|---|---|
| 目标人物关注列表已采 | 62 / 62 |
| 收集到的关注边 | 152,885 |
| 补采公开资料 | 4,642 个账号（100% 解析成功） |
| 可 BD 池 | 5,196 |
| 优先询价 / 先确认意愿 / 补商务路径 / 仅观察 | 1 / 157 / 3,909 / 1,129 |
| 已有报价、可直接推荐 | 276 |

结果落在 [reports/](../reports/)。唯一进入「优先询价」的是 **@__mharrison__**
（被 @fchollet、@jeremyphoward、@rasbt、@simonw、@yoheinakajima 五位关注，
简介写着 "DM for Sponsorship"）—— 数量少是正确的：它要求内容适配 **且** 有
承接声明 **且** 有可执行联系路径同时成立。剩下三档不是废弃，是三种不同的
待办工作。


---

## Root 库按领域分套

`GET /api/roots?domainGroup=ai|crypto|finance` —— 不同领域的客户看到不同的人。

| 领域 | 目标人物 | 圈层 | 来源 |
|---|---|---|---|
| `ai` | 62 | 行业超级大佬 14 · 美国 VC 中高层 30 · 垂类专家 18 | iLands 客户名单 |
| `crypto` | 28 | 行业超级大佬 8 · 加密 VC 与基金 10 · 垂类专家 10 | 种子提案 + 接口核验 |
| `finance` | 17 | 行业超级大佬 5 · 机构投资人与分析师 6 · 垂类专家 6 | 种子提案 + 接口核验 |

crypto / finance 的名单来自 [config/root_seeds.json](../../config/root_seeds.json) ——
**一份可编辑的提案，不是结论**。改完重跑 `build_root_library.py` 即可，脚本幂等。

三条纪律：

- **每个名字都过接口核验。** 账号查不到、改过名、读不到的直接跳过并报出来，
  绝不建 `handle:xxx` 占位记录 —— 占位 rest_id 曾让整张关系图静默返回全零。
  首轮 48 个种子里有 3 个没通过，被丢掉了。
- **`root_type` 留空，`root_review_status` 是 `pending`。** 谁是这个领域的目标
  人物是人的判断。种子里的理由存进 `source_notes_raw` 并永久标 `unverified`，
  筛选逻辑一行都不读。
- **不用 roster centrality 反推。** 库里只有 11 位 crypto、4 位 finance 创作者，
  用他们的共同关注代表整个圈子，得到的是「Mango 恰好签了谁」的放大版。

```bash
.venv/bin/python -m signal_map.scripts.build_root_library --domain crypto --dry-run
.venv/bin/python -m signal_map.scripts.build_root_library --all
.venv/bin/python -m signal_map.scripts.collect_target_following --from-candidates --domain crypto
.venv/bin/python -m signal_map.scripts.enrich_cofollowed_profiles --min-targets 4
```

### 领域切换的实测差异

同一个客户，三个领域各用自己的 Root 库：

| | priced | discovered | 报价库中同方向 | discovered 前列 |
|---|---|---|---|---|
| ai | 12 | 6 | 247 / 276 | lachygroom · fmanjoo · jasonfried |
| crypto | 8 | 4 | **8 / 276** | HesterPeirce · RAC · MPtherealmvp |
| finance | 4 | 3 | **5 / 276** | kylascan · BenEisen · _SidVerma |

`pricedSupply` 会把这件事说出来：crypto / finance 方向的现有报价资源很薄，
名单以新发现对象为主。不说明白的话，一个金融项目看到 4 位 AI 创作者，会读成
「Mango 的金融资源就这些」。


---

## 身份分类：这个账号卖不卖内容位

`classify_account` / [classify_account_roles.py](../scripts/classify_account_roles.py)

@HesterPeirce（SEC 委员）出现在了客户的可投放候选里。在那之前用简介关键词清过
四轮 —— CEO、记者、会议号、feedback 邮箱、职务信箱 —— 每轮清掉一批，下一批数据
换个形态又冒出来。**关键词分不清「有影响力」和「售卖内容位」**：这两件事在简介
里长得一模一样，一个人加一份履历。

模型只回答一个问题：**Mango 能不能付钱让这个账号发东西？**

| 判定 | 含义 | 可投放 |
|---|---|---|
| `creator` | 个人创作者，售卖内容位 | ✅ |
| `media` | 媒体 / newsletter / 播客，按库存售卖 | ✅ |
| `public_figure` | 创始人 / 高管 / 投资人 / 监管者 / 学者 / 在职记者 | ❌ |
| `organization` | 公司 / 产品 / 会议 / 机构官方号 | ❌ |
| `unclear` | 判不出来 | 不排除任何人 |

按**角色**判，不按名气、粉丝量或有没有联系方式判。实测：

```
@HesterPeirce   public_figure  「SEC Commissioner since 1/2018」
@BenEisen       public_figure  「Personal Finance Bureau Chief at WSJ」
@MPtherealmvp   public_figure  「brand partnerships at @aave」   ← 他在机构里做品牌
                                                                  合作，不是卖自己的位
@RAC            creator        「grammy award winning recording artist」
@lexfridman     creator        「Host of Lex Fridman Podcast」
@benthompson    creator        「Author/Founder of @stratechery」
@OpenAI         organization   「OpenAI's mission is to ensure...」
```

### 护栏

* 闭集校验；**每个判断必须引用原文**，没引用降级为 `unclear`。
* `unclear` 是正常且频繁的正确答案，**永不覆盖已有值**。
* 写进 `object_kind_suggested`，**不是** `object_kind` —— 建议和人的判定分列，
  和 `root_suggested_type` 同一条纪律。
* 缓存键含 prompt 指纹：改了 prompt，旧答案不复用。

### 为什么客户面可以读这条建议

一般规则是「机器建议不参与判断」。这里破例，**只在一个方向上**：建议只用来把人
从可投放名单里**拿掉**，永远不用来放进去。两个方向代价不对称 —— 错误排除一个可买
的人，损失一次机会；错误放进一个 SEC 委员，是产品级别的尴尬。`unclear` 不排除
任何人。人的判定永远压过建议（`effective_object_kind`）。

```bash
.venv/bin/python -m signal_map.scripts.classify_account_roles --dry-run
.venv/bin/python -m signal_map.scripts.classify_account_roles --min-targets 6
.venv/bin/python -m signal_map.scripts.classify_account_roles --handles HesterPeirce,RAC
```

**一次只能跑一个写进程。** 这条在 CLAUDE.md §10a 里记着，我又犯了一次：
后台扫描跑着的时候另开了一个指定 handle 的运行，撞出 UNIQUE 冲突。


---

## 交付质量：不给客户半成品

一轮针对「卡片读起来像没做完」的整改。改之前的样例里：**18/18 张卡没有头像**，
1/3 没有内容行，发现候选里 57% 既没有身份判定也读不出领域 —— 卡上只剩 handle
和粉丝数。

| | 改前 | 改后 |
|---|---|---|
| 头像 | 0 / 18 | **15 / 15** |
| 一句话内容 | 12 / 18 | **15 / 15** |
| 领域可命名 | 约一半 | **100%（进名单的）** |
| 未经身份检查就出现在客户面前 | 有（@BChappatta） | **0** |

四件事：

1. **头像**：`backfill_avatars` 从**已缓存的**接口响应里回填 15,412 个，
   **一次新请求都没发** —— 字段一直在我们付过费的响应里躺着。`_normal.jpg`
   换成 `_400x400.jpg`，同样不产生请求。
   缓存有**两个目录**（`data/cache/rapidx` 和 `signal_map/data/cache/rapidx`），
   只扫一个会漏掉 12,564 个。
2. **一句话内容**：领域 → 身份判定的引用原文 → 留空。永远不编。
3. **领域**：`classify_verticals` 给可投放账号补领域。简介关键词只对约一半的
   候选推得出领域，剩下那一半 `domains` 为空，**任何领域过滤都拦不住他们**。
4. **实质性闸门**：见 CLAUDE.md §5a。

### 邮箱用途的判断顺序就是证据强度顺序

顺序本身是逻辑的一部分，调换会出错：

| 顺序 | 规则 | 实例 |
|---|---|---|
| 1 | 邮箱前的**显式商务标签** | `Ads ads@unchained.com` → 商务 |
| 2 | 域名 = 自述的雇主 | `Dan@axios.com` + 简介 `@Axios` → 工作 |
| 3 | 通用/媒体信箱 | `feedback@`、`Press:` → 非商务 |
| 4 | 自述职业 | `reporter`、`editor at` → 工作 |

@laurashin 自称 "Crypto journalist"、邮箱域名又是自己播客的域名 —— 规则 2 和 4
都会把她判成买不到。但她简介里写着 **Ads**，她卖的正是播客广告位。规则 1 必须
在最前面。


---

## 近期内容表现：粉丝数骗人的地方

`enrich_recent_performance` 采最近 20 条**原创帖**（不含转推）的播放中位数、
条数和最近发布日期，只对能进客户名单的账号跑（434 个已采）。

```
@WSJ     22,193,119 粉  中位播放  26,176
@MKBHD    5,000,000 粉  中位播放 666,156
```

**差一个量级。** 粉丝数说明的是历史积累，播放中位数说明的是**现在还有多少人
在看** —— 客户判断值不值这个价看的是后者。名单上只给粉丝数，等于把最关键的
那个数藏起来。

* **中位数不是平均数**：一条爆款会把均值抬到看不出常态。
* **只算原创帖**：转推的播放量属于原作者，混进来会让天天转发的账号看起来很有量。
* **`latestPostAt` 同时回答「还活跃吗」**：半年前的中位数和上周的中位数不是
  同一种信息。@taylorswift13 采到的最近发布是三个月前 —— 那本身就是判断依据。

---

## 间接关系：不是只有硬关系才算

直接关注不是唯一有意义的关系。新增 `shared_circle` 链路：

```
目标人物 → 关注 → 我方创作者 → 关注 → 候选
```

一个候选被「你的目标所关注的那几位创作者」共同关注，说明他在同一个注意力圈层
里。实测：188 位创作者里有 25 位被所选目标关注（可作中间人），经他们二跳可达
**18,720 个一跳够不到的账号**，其中被 ≥2 位中间人关注的 1,062 个。

**但它比直接关注弱一档，措辞和权重都体现这一点：**

| | 直接关注 | 同圈层（间接） |
|---|---|---|
| `type` | `follow` | `shared_circle` |
| `strength` | weak / medium | **永远 weak** |
| 排序权重 | 1.0 | **0.35** |
| 标签 | 「单向关注」 | 「同圈层：你的目标关注的创作者也在关注他（**间接关系**）」 |
| `via` | — | 经由哪几位创作者，可追查 |

中间人只取**目标人物真的关注了的**我方创作者。不加这层过滤就退化成
roster centrality —— 那反映的是 Mango 签了谁。

实测价值在**窄目标场景**：只选 @karpathy + @rasbt + @simonw 时，三位候选经由
@TheTuringPost、@LinusEkenstam 浮现，其中包括 **@mreflow（Matt Wolfe，
100 万订阅）**。目标选得多时直接关注就填满了名单，间接关系自然排在后面。

`includeSharedCircle: false` 可关闭；`minBridges` 默认 2（1 位太噪）。


---

## 客户决策闭环

之前断掉的一环：客户能看到新发现的人，却没法在同一个流程里说「我要这个」。

```
POST /api/projects/{id}/feedback   {candidateId, action, reasonCode?, note?}
GET  /api/projects/{id}/shortlist
```

| 动作 | 含义 | 产生的内部工作 |
|---|---|---|
| `interest` | 「我感兴趣，你去谈」—— **新发现对象没有价格** | `client_interest` 优先建联 |
| `approve` | 「我要了」—— 前提是价格档期能确认 | `price_inquiry` 落实报价 |
| `question` | 有疑问 | `client_question` |
| `shortlist` / `unshortlist` | 加入 / 移出名单 | — |
| `reject` | **仅本项目**移出，不动供给层 | — |

`interest` 单独存在，是因为逼客户对一个没有报价的人做 approve 不合理 —— 而那
恰恰是新发现对象的常态。它也是「客户的选择驱动资源库扩张」那条闭环的起点。

表达兴趣会把账号**升级成可复用的供给记录**（`Creator`，id ≥ 1,000,000），
并把已判出的领域、受众、媒体/创作者身份一起带过去。不带的话，同一个人在候选卡
上有角色有领域、进了名单就全空 —— 客户看到同一个人在两个页面上不一样。

响应里带 `effect`，让客户看到动作产生了后果：

```json
{"action":"interest","recorded":true,"internalTaskRaised":true,
 "effect":"已转为 Mango 的优先建联任务，我们会去接洽并取得报价"}
```

### 名单视图

`GET /shortlist` 返回预算两段 + **角色构成**：

```json
{"budget":{"known":{"lo":150,"hi":500},"pendingCount":1},
 "coverage":{"byRole":{"专业验证者":1,"媒体节点":1},
             "expandable":["解释者","回声节点"],
             "note":"当前名单已覆盖 专业验证者、媒体节点；下一步可拓展：解释者、回声节点"}}
```

八个专业验证者不是一个组合，是同一次下注重复了八遍 —— 角色构成就是「阵容」和
「一堆人」的区别。

---

## 传播角色：覆盖新发现的人

`portfolio._creator_role` 只吃 `creator_tier`（BD 时期的分类），新发现的账号
没有这个字段，于是**角色一栏对半数名单是空的**。`projects_api.creator_role`
改成吃任何一种可得的证据，顺序是：

**他是什么 → 他触达谁 → 他有多大**

```
@latentspacepod  媒体节点    账号性质为媒体/栏目，按库存采购而非个人声量
@ClaireSilver    解释者      受众集中在 设计师
@_akhaliq        专业验证者   受众集中在 研究员
@dwarkesh_sp     引爆者      近期单帖播放中位数 202,032
@DataRepublican  引爆者      近期单帖播放中位数 139,790
```

顺序反过来会让量级压倒性质，把每个大号都叫引爆者 —— 那正是这个产品要替代的
单维判断。**引爆者按播放中位数判，不按粉丝数**：百万粉但单帖一万播放的账号
引爆不了任何东西。

八类里目前能判六类。`引爆者` 之外的 `社区节点` 需要社群数据，还没有，所以
不假装能判 —— 判不出来就是 `待定角色`。


---

## AI FrontRun · 重要人物最近开始关注了谁

```
GET /api/internal/bd/frontrun/deltas?domainGroup=ai&windowDays=7&minWatchers=2
GET /api/internal/bd/frontrun/coverage
```

**内部接口。** CLAUDE.md §11：共用数据层，**不共用客户界面** —— 这是 Mango
自己的情报面，不是卖给某一个客户的东西。观察名单就是 Signal Map 里标记为
目标人物的那批账号，两个产品共用同一批人，各问各的问题。

### 守卫是这个模块唯一的正确性来源

`first_seen_at` 的含义是 **Mango 首次观察到这条边**，不是「这个关注是那天开始
的」—— X 不暴露后者。所以：

| 情况 | 处理 |
|---|---|
| 没有更早的完整快照 | `first_collection`，**不报任何新关注** |
| 上一次采集被页数截断 | 不作基线 —— 会把没翻到的页算成新关注 |
| 上一次采集报错 | 不作基线 |
| 同日重采 | `same_day_recollect`，间隔不足以判断 |
| 取关 | 只在**前后两次都完整**时才认 |

不加守卫，首采会输出「Elon 刚刚关注了 1,405 个账号」。那是假消息。

**没有基线时返回的是状态，不是空列表** —— 空列表会被读成「最近没动静」，
而实际是「我们还没法比」。

### 首次真实产出（2026-09-09 → 09-11，8 位观察对象）

```
@paulfchristiano  15.0K ← @karpathy        Alignment Research Center 创始人，前 OpenAI Head of Safety
@EvanHub          23.2K ← @simonw          Alignment Science lead @AnthropicAI
@tianyi           43.6K ← @jeremyphoward   Member of Technical Staff @ DeepSeek
@gabepereyra      11.9K ← @swyx            building @harvey
@ambricken         2.6K ← @swyx            Trying to make things go well @ClaudeAI
```

外加 5 次取关。**两天窗口里，AI 安全/对齐方向的人被集中新关注** —— 这正是
FrontRun 要抓的那种信号。

### 每条结论都带限制，不是页脚免责声明

```json
{"handle":"paulfchristiano","resolved":true,"watcherCount":1,"converged":false,
 "newlyFollowedBy":[{"watcher":"karpathy","observedAt":"2026-09-11","sinceDays":2}],
 "claimLimit":"「首次观察到」不等于「刚刚开始关注」：X 不暴露关注的发生时间，这里只能说明上一次采集时还没有这条关注"}
```

`converged`（被 ≥2 位观察对象在同一窗口新关注）排在最前：一个人开始关注某个
账号是日常，两位重要人物同时开始关注才值得看一眼。

### 用法

```bash
# 每周跑一次就有持续产出。第二次之后才有 delta。
.venv/bin/python -m signal_map.scripts.collect_target_following --from-candidates
# 新关注里只见过 id 的，批量补资料（「他关注了谁」答不出「谁」就没有答案）
.venv/bin/python -m signal_map.scripts.enrich_cofollowed_profiles --frontrun
```
