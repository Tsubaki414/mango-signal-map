# CLAUDE.md — Mango Signal Map

**Mango Signal Map is the product being built in this repo.**

The existing Mango BD system in [kol_database/](kol_database/) is the **previous generation** — a research and BD-intelligence tool for Mango's own team. It is being retired. Treat it as a **parts source**: real data worth inheriting, some code worth lifting, some hard-won lessons worth keeping. It is **not** a running system Signal Map must fit around, extend, or avoid breaking.

Read §3 before assuming any constraint from the old system still applies. Several do not.

Related docs: [AGENTS.md](AGENTS.md) (evidence contract — still binding), [kol_database/docs/product-language.md](kol_database/docs/product-language.md) (wording discipline — still binding), [PLAN.md](PLAN.md) (old BD workstreams — historical).

---

## 1. What Signal Map is

Traditional KOL service hands the client a spreadsheet of accounts, followers, platforms and prices, and lets them pick from a list of hundreds. Signal Map replaces that with **注意力设计与投放决策系统**.

> 不只是选择谁来发布内容，而是设计希望谁注意到项目、通过什么路径注意到、由谁解释和背书、以什么内容进入其信息流，以及如何用预算组成最合理的传播组合。

The client buys Mango's synthesized judgment across 目标人群 / 行业圈层 / 内容叙事 / 人物关系 / KOL 角色 / 真实报价 / 商务路径 / 预算组合 / 执行风险 / 投放结果 — not a batch of accounts.

Three things the client should feel: **被理解 · 有掌控感 · 看到了自己无法独立获得的信息和判断**.

### One product, two surfaces

This is the structural difference from the old BD tool, and the thing most likely to be got wrong:

| | 内部作业面 | 客户决策面 |
|---|---|---|
| Who | Mango team | the client |
| Does | 供给管理、报价与议价、商务路径、档期、执行、复盘、内部判断 | 目标设置、路径展示、KOL 选择、比较、反馈 |
| Sees | everything | only what was explicitly cleared |

Both surfaces belong to Signal Map. There is no third system behind it to fall back on — when the spec says "Question 转成内部行动项" or "进入询价任务", the destination is Signal Map's own internal surface, which must therefore exist.

Field isolation between the two surfaces is enforced in the backend serializer, never in the frontend (§6).

### Client experience Signal Map builds toward

项目理解 (产品/阶段/市场/语言/目标/竞品/热点/饱和叙事/风险) → 目标圈层与 Root 选择 → 注意力路径展示 → KOL 角色设计 (引爆者/解释者/专业验证者/圈层桥梁/转化推动者/回声节点/媒体节点/社区节点) → 传播组合与预算方案比较 → Approve/Reject/Question 共同决策 → 商业执行 → 沿最初目标复盘.

The MVP (§5) is the first runnable slice of this, not a different thing.

## 2. The four paths — never merge these

The single most important correction in this project. Four different relationships answer four different questions, use different evidence, face different users. **They must not share a field or a table**, even when the same person appears in two of them.

| Path | Question | Status | Client-visible? |
|---|---|---|---|
| **1. Mango 商务触达路径** | How does Mango reach a target? (员工 → 同事/介绍人 → 联系人 → 对象) | Old system has real data + schema worth lifting | **No** |
| **2. 商业采购路径** | If the client buys this KOL, who does Mango buy through? (KOL → 经理人/Agency/MCN/供应商 → Mango) | **Out of scope** — every priced creator was contacted by Mango directly and agreed to work with us, so the route is `direct` | Only as "由 Mango 对接" |
| **3. KOL–Root 注意力路径** | Why might this KOL put the client into a target Root's feed? | **Built, partial** — 4,888 follow + 52 interaction signals, 249 candidate accounts, 176/188 creators. All 研究线索 (bare follows). **0 of the 249 human-reviewed**, and a machine pass suggests ~73% are not Roots at all. See [root-graph-plan.md](signal_map/docs/root-graph-plan.md) §6b | Verified + desensitized parts only |
| **4. 投放与转化路径** | What did real audiences actually do after publication? | Not started; accrues per campaign | That client's own results only |

Hard rules:

- The old system's "路径 / 可触达等级 / 介绍关系" fields are **path 1 only**. Never read them as Root relationships.
- Never fill a client-facing relationship graph with Mango employees' BD paths. If no real KOL–Root Signal exists, show "关系数据待补充" or hide the module.
- A follow proves a follow Signal exists. Not endorsement, not exposure, not timing, not conversion.
- Zero results = "当前未观察到", never "两者无关系".
- Without real exposure data, never turn Signals into a "被看到概率".
- Every doc, field name, API response and UI string must say **which** path it means. The bare phrase "触达路径" is banned.

Path-3 records, when built, need: source node, target node, platform, signal type, direction, occurred-at, observation count, raw evidence, collected-at, completeness, confidence, and any employment/ownership/interest conflict.

Path 1 and path 2 are internal-surface concepts. Path 3 is the new strategic capability. Path 4 only exists after campaigns run.

## 3. What to salvage from Mango BD

Audited 2026-09-07 against `kol_database/data/kol.db` and the live Railway deployment.

### Data — inherit it, it is the moat

Real, hand-collected, expensive to reproduce. Do not re-collect it.

| | Rows | Notes |
|---|---|---|
| creators | 357 | **301 have ≥1 rate card** |
| rate_cards | 699 | 608 `is_confident=1`; `raw_quote_text` preserved verbatim |
| social_accounts | 360 | enriched: followers, avg/median views, engagement, posting freq |
| contact_methods | 392 | internal only, forever |
| companies | 99 | |
| sponsorship_evidence | 93 | 历史合作证据 |
| intro_bridges / interaction_evidence | 74 / 4 | path 1 |
| action_items | 42 | |
| shortlists / outreach_logs | 0 / 0 | empty — test data was purged |

Priced objects: **301 deduplicated**. Currency USD 677 / GBP 11 / EUR 11. Platforms X 477, YouTube 152, Instagram 57, TikTok 10, Threads/LinkedIn/Facebook 1 each. Priced creators by class: KOL 141, Marketing Account 60, KOC 60, Unknown 32, Top KOL 3, Media/Community 3, Community Leader 2.

金融 KOL 不在本期范围 — no quotes yet, so a "has a real price record" filter excludes them naturally. Don't special-case them, don't delete them.

### Code worth lifting

| Module | Why |
|---|---|
| [campaign_matching.py](kol_database/backend/campaign_matching.py) | role-based matching with **field-level evidence**, and it reports supply gaps instead of filling them with generic accounts — exactly the "never one unexplainable score" requirement |
| [parsing.py](kol_database/backend/parsing.py), [fx.py](kol_database/backend/fx.py) | quote text → structured amount/range; currency handling |
| [classify.py](kol_database/backend/classify.py), [bd_creator_classify.py](kol_database/backend/bd_creator_classify.py) | creator classification with manual-lock-wins semantics |
| [rapidx_client.py](kol_database/backend/rapidx_client.py), [youtube_client.py](kol_database/backend/youtube_client.py), [scrapecreators_client.py](kol_database/backend/scrapecreators_client.py) + enrichment modules | working API clients for X / YouTube / IG / TikTok |
| [importer.py](kol_database/backend/importer.py) | sheet ingestion with provenance |

### Schema worth studying (reference, not blueprint)

`Creator`/`SocialAccount` split ([models.py:78](kol_database/backend/models.py#L78)) is sound. `Shortlist` ([models.py:258](kol_database/backend/models.py#L258)) already merged brief + candidate list + budget and it worked — study why before designing Signal Map's equivalent. `OutreachLog`'s funnel-event + void model, and E0–E6 relationship staging, are hard-won and worth carrying.

### Constraints from the old system that **no longer apply**

I got these wrong on the first pass. State them plainly so nobody re-adopts them:

- ❌ "Schema changes must be additive-only" — that existed to keep BD's pages alive.
- ❌ "Must not break existing internal pages / workflows."
- ❌ "`kol_database/tests/` is a release gate."
- ❌ "Never rebuild BD pages" — Signal Map's internal surface *is* the replacement.
- ❌ "Signal Map is a layer on top of BD."

Signal Map's schema is designed from the client product's needs. `kol.db` is its **starting dataset**, not its shape.

### Still binding

- ✅ Don't duplicate the data or re-collect it.
- ✅ Don't build a parallel creator/company/quote master — one truth per entity.
- ✅ Evidence contract in [AGENTS.md](AGENTS.md): every material claim carries source, date, `last_verified_at`, evidence type, confidence, fact status.
- ✅ Wording discipline in [product-language.md](kol_database/docs/product-language.md) (§8).
- ✅ Never fabricate. 缺失数据显示未知.

### Worst data gaps

1. **Quote metadata was missing entirely.** BD's `RateCard` had amount, currency, platform, `raw_quote_text` — and **no** source, quote date, validity, confirmation status, internal cost, client price, or display permission. Fixed by §4's `Quote` model; the values themselves still need human review.
2. **No measured audience geography.** `audience_markets` is empty and stays empty until real research fills it. The coarse `market_region` bucket covers the filter (see below) but is an inference, not a measurement.
3. `language` / `region` / `categories` arrived un-normalised; fixed in [normalize.py](signal_map/backend/normalize.py), with raw values preserved for re-derivation.

Coverage of the two filter fields, over the **262 priced creators**:

| | before | after derivation | after enrichment |
|---|---|---|---|
| `market_region` | 42 (country only) | 238 | **261** (99.6%) |
| `audience_types` | 0 | 229 | **257** (98.1%) — 257 via LLM |
| `creator_tier` (KOL/泛KOC) | not carried over | **233** | 233 |

### Coarse-but-filterable beats precise-but-empty

A precise field nobody can populate silently deletes inventory. A
country-based market filter matched **42 of 262** priced creators and would
have hidden the other 84%; 目标人群 matched **zero**. So both are coarse
buckets derived down an evidence ladder:

- **`market_region`** (欧美 / 拉美 / 中文圈 / 日韩 / 东南亚 / 南亚 / 中东 /
  非洲 / 全球): content language → creator country → source-sheet provenance →
  Latin-script bio. 英文 ⇒ 欧美, per product direction.
- **`audience_types`** (创始人 / 开发者 / 交易者 / 营销 / 设计师 / …): the
  creator's own bio and posts → their verticals. Capped at three, because a
  creator who reaches everyone is not a useful filter result.

This does **not** loosen the no-fabrication rule. Four constraints hold it in
place, and any similar coarse field must do the same:

- **The basis is stored and shown.** `market_region_basis` /
  `audience_types_basis` name the rung that produced the value, so a card
  reads 依据：内容语言 rather than implying Mango measured anything. This is
  高概率推断, never 已验证事实.
- **The evidence is stored.** `audience_types_evidence` quotes the phrase
  behind each type (`founders:"passive income"`). Some inputs — a creator's
  recent posts — are transient, so without this the judgment would be
  permanently unauditable, which this product cannot ship.
- **Inference and measurement stay in different columns.** `market_region`
  (derived) never writes to `audience_markets` (observed). Collapsing them
  would let a guess be read as a fact.
- **Unknown is never silently excluded.** A creator with no signal still
  appears in filtered results, labelled 市场待确认 / 人群待确认. Same rule as
  Root Signals: absent data lowers confidence, it never deletes a candidate.

Content language is also the *better* market proxy regardless of coverage — an
English-language creator reaches a Western audience whether they are based in
Singapore or Texas. Where a creator lives answers a different question from
which market their content lands in, so language leads and country only fills
the gap. For the same reason `ai` is deliberately absent from the
vertical→audience map: an AI account may address developers or consumers, and
treating "AI" as an audience signal was the old recommender's mistake.

### KOL vs 泛KOC

BD's creator taxonomy is a real commercial distinction and is carried over
whole, as a filterable `creator_tier`: **strategic** (Top KOL / Community
Leader / KOL — bought for voice and credibility), **distribution** (KOC /
Marketing Account — bought for reach and cost), **media**, **non_creator**,
**unknown**. Priced inventory splits **132 strategic / 98 distribution / 3
media / 29 unclassified**.

`non_creator` is in `NON_RECOMMENDABLE_TIERS` and **gates the candidate pool**
— a news outlet or bot has no audience relationship to sell, so it must be
excluded, not merely ranked lower. A price alone never makes something
recommendable.

### Enrichment

Two scripts, by platform, both cached and `--limit`-capped. A profile that
cannot be read stays 待确认 and is reported — never guessed at.

- [enrich_unknown_profiles.py](signal_map/scripts/enrich_unknown_profiles.py)
  — **X via Rapid X**, the only sanctioned X source per [AGENTS.md](AGENTS.md).
  Reads bio *and recent posts*: a bio is a slogan ("Turning AI into money
  machines") and often says nothing about who the account addresses.
- [enrich_youtube_apify.py](signal_map/scripts/enrich_youtube_apify.py) —
  **YouTube via Apify**. There is no `YOUTUBE_API_KEY` and these creators have
  no X presence at all. Returns full About text, recent **video titles**,
  subscriber count and `channelLocation` (a stated country, stronger than any
  guess).

Actor choice was settled by testing, not store ranking:
`apidojo/youtube-channel-information-scraper` returned nothing for 14 of 23
channels that all resolve fine in a browser;
`streamers/youtube-channel-scraper` returned every one and adds video titles.

### LLM classification — live

Keyword matching is not good enough for audience. It produced
`OtakuMachine → creators` because the bio said "edits" — but an anime-edits
channel is watched by *anime fans*, not editors. The regex was matching **what
the creator does** and labelling it **who watches**. That is a semantic
distinction no pattern list fixes.

[llm_classify.py](signal_map/backend/llm_classify.py) replaces it, under
guardrails that keep it auditable: output validated against the closed
`AUDIENCE_TYPES` vocabulary, **every type must quote the content that
justified it** (no quote → dropped), empty is an explicitly valid answer that
never overwrites an existing value, and model id + evidence are stored.
The prompt forbids citing business-contact lines ("Brand collaborations open",
"📩 DM for partnerships") — those say who a creator *works with*, not who
watches, and appear on nearly every account.

Provider-agnostic — **xAI (Grok)** or **OpenAI**, same schema, xAI preferred:

```
XAI_API_KEY    + XAI_MODEL     -> api.x.ai       (grok-4.20-0309-non-reasoning)
OPENAI_API_KEY + OPENAI_MODEL  -> api.openai.com (gpt-4o-mini)
```

Default is the **non-reasoning** variant deliberately: this is closed-vocabulary
extraction at temperature 0, where reasoning costs more and adds nothing.

Run it with
[classify_audience_llm.py](signal_map/scripts/classify_audience_llm.py). It
covers **every priced creator, not just unresolved ones** — the regex verdicts
were wrong in kind, not merely absent.

The verdict cache keys on content **+ model + prompt fingerprint**. Editing the
prompt changes what a verdict means, so cached answers from an older prompt
must never be reused — this regressed once and is now covered by a test.

This does not contradict "recommendation reasons are never model-authored" —
that rule governs the reason text shown to a client, which stays rule-generated
from stored fields. This is structured extraction into a fixed enum.

## 4. Quote model

Quotes are the core asset. Never overwrite or lose the original text.

Model at minimum: 原始报价内容 · 金额或区间 · 币种 · 平台 · 内容形式 · 数量 · 是否套餐 · 报价来源 · 报价时间 · 有效期 · 确认状态 · 是否需要复核 · 内部成本 · 客户报价 · 是否允许客户端展示.

Quote nature must survive into the client layer: 已确认且仍有效 / 已确认但可能过期 / 历史成交 / 供应商提供 / 公开报价 / 内部估算 / 需要复核.

### Bare numbers are prices, at low confidence

39 creators carried **no price at all** because their quote message read `300`
or `Single 250; Thread 400`. The bare-amount scanner required a currency marker
or a price cue (`per`/`for`/`:`), correctly so — `7-10 工作日` and `30-50%` must
never read as money. But that guard also deleted ~15% of the priced inventory
from the candidate pool, invisibly.

The fallback fires **only when the message names no currency anywhere**, which
is what separates a bare `300` from a `7-10` sitting inside a message that
already prices things in dollars. A numeric floor was considered and rejected:
the real minimum quote here is $20.

These parse at `low` confidence, flagged `amount_inferred_from_message_shape`,
`needs_review`, and **never `client_visible`** — the client sees a band and
报价需复核, Mango confirms before it is quoted or paid. Result: **+14 priced
creators (262 → 276)** and the review queue halved (48 → 24), because a decline
(`Not provided`, `Flexible; negotiable`) is now a terminal `no_quote` rather
than review work nobody can finish.

### Provenance is settled; parse quality is the gate

**Every quote in this dataset was collected first-hand by Mango, asking
creators for their rates** — confirmed by the project owner, who did the
collection. So `quote_source = kol_direct` is a *recorded* fact, not an
inference, and the missing per-row quote date is **not** a reason to withhold a
price. (Messages naming a manager or agency are upgraded to `manager_agency`;
that part is read from the text, so it is flagged inferred.)

What still gates a client surface is **parse quality**, which is a separate
question from provenance: 734 priced quotes parse at high/medium confidence and
are `confirmed_valid` + `client_visible`; 35 unparsed and 13 declined-to-quote
rows are not.

### Cost is not price

The quoted figure is what the creator asked **Mango** for — it is Mango's
**cost**. It is written to `internal_cost_usd` and **never** to
`client_price_usd`; copying it across would expose the cost and leave no
margin.

Until Mango sets a markup, the client sees a **band** (`client_price_band`,
e.g. `$1,000–2,500`) — the spec's 参考区间. Bands are deliberately wide: one
narrow enough to back-solve the cost would defeat the point. `client_price_usd`
stays NULL, so `client_display_price` returns None and the surface reads
价格待 Mango 确认 rather than inventing a number.

```
internal:  $1,300  (internal_cost_usd — never serialised to a client)
client:    $1,000–2,500  (client_price_band)
exact:     None → 价格待 Mango 确认
```

If quotes can't be summed honestly (packages, mixed currencies, unconfirmed), output **"需要 Mango 确认"**. Never fabricate a precise total.

## 5. MVP scope

**Superseded 2026-09-09 — read §5a first.** The flow below described selecting
from the **priced library**. That was wrong in kind: it made the product a
catalogue with filters. 候选池不等于报价库. The method is target-backed
discovery, and the priced library is only the fastest-executing slice of it.

```
客户说出想影响谁（目标人物 / root）
  → 取这些人的关注与互动网络，求共同子集
  → 候选池：一部分已在报价库（可立即确认），一部分从未建联（客户表达兴趣后去建联）
  → 三道过滤：真实流量 / 可合作路径 / 内容表达面
  → 客户可见的执行池 → Approve / Reject / Question / 表达兴趣
  → 进入 Mango 内部作业面：建联、询价、复核、推进
```

Success = a client names the people they want to influence, and the backend
returns an explainable, budget-executable set of recommendations **spanning both
already-priced objects and newly discovered ones**, persists their choices, and
turns their interest into BD priority.

In scope: priced-object view · quote normalization · client preferences · recommendation + explanation · budget rollup · 候选名单 · Approve/Reject/Question · client-safe output layer · stable API for Lovable · extension seams for Root Signals.

### 5a. target-backed discovery — built

[projects_api.py](signal_map/backend/projects_api.py) · sample:
[projects_candidates.json](signal_map/docs/api_samples/projects_candidates.json)

**候选池不等于报价库。** ``GET /api/projects/{id}/candidates`` returns two kinds
in one list, distinguished by ``source``:

* ``priced`` — already in the quote library, tier and schedule confirmable now
* ``discovered`` — computed from the targets' follow/interaction network,
  ``price: null``, needs BD. This is the product's core value proposition and
  the entry point through which Mango's own library grows: the client's picks
  drive BD priority.

Rules that took several wrong turns to arrive at:

- **The targets are people you cannot buy.** Elon Musk, Sam Altman, a16z GPs.
  The whole Root library is excluded from the candidate pool — **not just the
  handles selected this round**. Whether someone is a target is a property of
  the person, not of this round's checkboxes. Getting that wrong put Andrew Ng
  and Jeff Dean in a client's buyable list.
- **Ranking is 连接数 × 连接强度**, never a composite score. Content, audience
  and market are secondary keys only, so it is always answerable whether
  someone ranks high because of connection or because of content.
  **No connection to the selected targets ⇒ not in the client list** (the
  internal BD queue still keeps them).
- **`LinkStrength` is strong/medium/weak.** A one-way follow is `weak` and
  nothing more: @pmarca follows 32,758 accounts. Only replies/quotes/reposts
  reach `strong`. No H0–H3 codes reach a client.
- **`BizState` is three-valued** (`ready` / `open_channel` / `needs_bd`), each
  carrying `bizEvidence` naming what was actually read.
- **Budget returns two parts** — `{known:{lo,hi}, pendingCount}`. An unpriced
  object is never folded in at an estimated value; that would read as "already
  quoted", and Mango has to honour every number a client sees.
- **`discovered` never renders as 已确认可合作**: `quoteStatus` is
  `pending_inquiry`, price is null.
- **A card the client cannot judge is not shippable.** Two gates, and the
  boundary between them matters. The *project filter* still excludes only on
  `mismatch` — `unknown` never eliminates. The *card-substance* gate is
  separate: a discovered candidate must have a **nameable content area** and
  must have been **identity-checked and found buyable**. Without the first,
  @bestofnextdoor (bio: "quality neighborhood drama") reached an AI project's
  list — no domain filter can catch what has no domain. Without the second,
  @BChappatta (Bloomberg columnist) reached a finance list because his bio
  yielded `finance` while nobody had ever looked at who he is. This is not
  "unknown eliminates"; it is "we do not hand over a card nobody has read".
  Those candidates stay in the internal BD queue, where the next step is to
  fill the profile in. `unclear` and `public_figure` are both absent from the
  client list and mean opposite things — one is a to-do, the other a verdict —
  which is why the suggestion column distinguishes *attempted*
  (`object_kind_suggested_by`) from *concluded* (`object_kind_suggested`).
- **A direct follow is not the only relationship worth showing.** `shared_circle`
  adds the two-hop path `target → follows → our creator → follows → candidate`:
  the candidate sits in the attention neighbourhood of creators the client's
  targets watch. It reaches 18,720 accounts one hop cannot, and in a narrow
  target selection it surfaced @mreflow (Matt Wolfe, 1M subs) via
  @TheTuringPost and @LinusEkenstam. **It is one notch weaker than a direct
  follow and every part of the output says so**: `strength` is always `weak`,
  the ranking weight is discounted to 0.35, the label reads 间接关系, and `via`
  names the bridging creators so the claim is checkable. Bridges are restricted
  to creators the targets actually follow — without that filter it degenerates
  into roster centrality.
- **Followers are history; median views are attention now.** @WSJ has 22.2M
  followers and a median of 26,176 views per post; @MKBHD has 5M and 666,156.
  An order of magnitude apart, so a card showing only follower count hides the
  number the client is actually buying. `enrich_recent_performance` reads the
  last 20 **original** posts (reposts excluded — those views belong to the
  original author) and stores the **median**, never the mean, plus
  `latestPostAt`, which answers "are they still active" separately.
- **Project type narrows the discovered pool, and only that pool.** Domain,
  market and language **mismatch** excludes; **unknown never does** — only ~47%
  of discovered bios yield a vertical, so dropping the undetermined would delete
  half the pool invisibly. The priced library is *not* content-filtered: it is
  Mango's finite inventory and the client is entitled to see who was considered.
  What the narrowing removed is reported in `narrowedByProject`, so a short list
  never reads as "there simply weren't more".

**The loop closes at `interest`, and `interest` is not `approve`.**
`POST /api/projects/{id}/feedback` accepts approve / reject / question /
**interest** / shortlist. The new action exists because a discovered candidate
**has no price**, and asking a client to *approve* something unpriced forces a
commitment they cannot make — which is the normal state for the half of the
pool that is the product's whole selling point. `approve` means "I'll take
this, confirm the price"; `interest` means "I want this one, go get them".
They raise different work (`price_inquiry` vs `client_interest`), and the
second is how **the client's choice becomes Mango's BD priority** — the loop
that grows the supply library.

Expressing interest **promotes the account to a reusable supply record**
(`Creator`, id ≥ 1,000,000). That is the right trigger: the client asking for
someone is exactly when Mango should go build that relationship, and once
built it should serve the next client too. The promotion **carries the
classification across** — verticals, audience, media/creator kind. Without
that, the same person shows a role and a domain on the candidate card and
nothing on the shortlist, and a client seeing one person render two ways is
the most trust-damaging kind of bug there is.

`reject` removes from **this project only**. Nothing a client does may change a
field in the supply layer.

**Roles cover discovered candidates, not just priced ones.**
`portfolio._creator_role` reads `creator_tier`, which newly discovered accounts
do not have, so the role column was blank for exactly the half of the list that
needed explaining. `projects_api.creator_role` reads whatever evidence exists —
LLM identity, verticals, audience, median views — in that order: **what they
are, then who they reach, then how big**. Reversed, size dominates and every
large account becomes 引爆者, which is the single-dimension judgment this
product exists to replace. 引爆者 is gated on **median views**, not followers:
a million-follower account with 10K views per post amplifies nothing.

**Emails hide inside `bio`.** The field allowlist blocked `contact_methods` and
then handed the same address to the client inside the profile text. `bio` is
redacted before serialisation. That is the *third* time a value leaked through
content rather than through a field name — see §10a.

**Identity is an LLM judgment, not a keyword match — solved 2026-09-09.**
Bio keyword rules could not separate a buyable creator from a journalist, a
conference account or a founder. Four passes of rules removed @Benioff, NYT and
Bloomberg reporters, @NeurIPSConf and role addresses; each pass cleared a batch
and the next shape of the data brought new ones. The two things look identical
in a bio: a person plus a résumé. Same class of problem as
`OtakuMachine → creators` in §3.

`classify_account` in [llm_classify.py](signal_map/backend/llm_classify.py) asks
one question — **can Mango pay this account to publish something?** — against
the closed set `creator / media / public_figure / organization / unclear`, under
the same §3 guardrails: every verdict quotes the content that justified it (no
quote → `unclear`), `unclear` is a frequent correct answer that never overwrites
anything, and the cache keys on content + model + prompt fingerprint. It reads
role, not fame: @HesterPeirce → *"SEC Commissioner since 1/2018"*, @BenEisen →
*"Personal Finance Bureau Chief at WSJ"*, and — the one a keyword never gets —
@MPtherealmvp → *"brand partnerships at @aave"*, someone who **works in** brand
partnerships rather than selling their own.

**The verdict goes in `object_kind_suggested`, never `object_kind`** — machine
suggestion and human decision in separate columns, same discipline as
`root_suggested_type`. The client surface does read the suggestion, which breaks
the usual "a suggestion never drives a judgment" rule. The exception is
deliberate and holds **in one direction only**: a suggestion may *remove*
someone from the buyable list, never add. The costs are asymmetric — wrongly
excluding a buyable creator loses one opportunity; wrongly including an SEC
commissioner is a product-level embarrassment. `unclear` excludes nobody.

Out of scope this round: full Root graph · the client frontend (Lovable is coming) · path 4.

The MVP must **not** claim: a complete KOL–Root graph · complete 圈层 coverage · proof of entering anyone's timeline · reliable 触达概率 · post-campaign conversion results.

### Recommendation logic — built

[recommend.py](signal_map/backend/recommend.py). Twelve axes judged
**separately**, all twelve returned with every result — 内容/行业 · 市场/语言 ·
平台 · 内容形式 · 目标人群 · 传播目标 · 预算可执行性 · 报价确定程度 ·
商业可执行性 · 历史合作 · 风险 · 数据可信度 · **注意力路径**.

The count is not fixed — Root signals were added as axis 13 exactly as
planned: a new axis, not a rewrite. Tests assert against `AXES` rather than a
literal number, so axis 14 will not break them.

Verdict per axis is one of `match` / `partial` / `mismatch` / `unknown` /
`not_asked`, each with a human-readable detail string and, where applicable,
the stored evidence behind it. A `rank_score` exists for ordering only; **no
surface may show it alone**, and `not_asked` axes are excluded from the score
rather than counted as zero.

Every result also returns: matched preferences · unmet conditions · missing
data · reasons · risk flags · next steps. **Reasons are templates filled from
stored fields** — a model never writes recommendation prose. (The LLM
classified `audience_types` upstream; here that is just another stored field
with quoted evidence.)

Three behaviours that look like bugs and are not:

- **`unknown` never eliminates.** Missing data lowers confidence and is listed
  in `missing_data`, never removes a candidate. Same rule as Root Signals.
- **`mismatch` never eliminates either.** A creator who misses a stated
  preference stays in the result with the miss in `unmet_conditions` — the
  client is entitled to see what Mango considered and why it ranked low.
- **Only three things remove a creator**, each counted and reported: no priced
  quote (cannot be budgeted), `non_creator` tier (no audience to sell), and an
  explicit client exclusion.

`must_include` creators are pinned to the top **with their axes intact**, so
pinning never hides a miss. Filters Mango cannot answer from held data are
returned in `unsupported_filters` rather than silently doing nothing.

Every run snapshots the brief, so a later brief edit cannot rewrite the meaning
of a batch the client already reacted to.

An object with no Root Signal is **not** eliminated. Root Signals are a future
*added* axis, not a rewrite.

### Budget

[budget.py](signal_map/backend/budget.py). **If quotes cannot be summed
honestly, output 需要 Mango 确认 rather than a confident number** — Mango has
to honour whatever the client is shown. Three things block a clean total and
are reported separately because each needs different follow-up: package prices
(not comparable with per-post prices), unconfirmed quotes (everything imported
from BD, which recorded no quote date or source), and mixed currencies
(convertible for planning, but a reference rate not an invoice).

An unpriced line is **never folded in at zero** — that reads as "free" rather
than "unknown" and understates what Mango must honour. `client_view=True` uses
only the cleared client price and never falls back to `amount_usd`, which may
be a supplier floor.

Each client-visible card should answer: 为什么推荐这个人 · 他适合影响谁 · 适合讲什么 · 承担什么传播角色 · 与目标圈层什么关系 · 报价与合作方式 · 历史表现 · 风险或利益关联 · 加入他组合新增了什么 · 为什么是他而不是相似的另一个人.

### Combination and budget

候选名单 add/remove · 比较 · 选择合作形式 · 预算汇总（保留原币种、标识确认状态、标出不能相加的套餐）· 名单版本 · 反馈重排序.

Structure must allow future 方案 types: 专业可信度 / 传播覆盖 / 产品转化 / 中英文跨境 / 头部与垂直混合.

### Feedback

Persist per **客户 + 项目 + 对象**: action · reason · notes · actor · timestamp · rule version · recommendation batch.

- Approve → 项目候选名单
- Reject → removed **from this project only**; never degrades the creator in the master data
- Question → 内部行动项 on Mango's operating surface
- 报价待确认 → 询价/复核任务
- 联系方式缺失 → 商务路径补充任务

Never browser-local-only state. Never a second isolated task system.

### 可 BD 筛选 — built

[bd_discovery.py](signal_map/backend/bd_discovery.py) · [bd_screening.py](signal_map/backend/bd_screening.py) · [bd_api.py](signal_map/backend/bd_api.py) · contract: [BD-SCREENING.md](signal_map/docs/BD-SCREENING.md).

The other half of the MVP: the priced pool answers "who can we sell today", this
answers "who should Mango go and sign". **One call, `POST /api/internal/bd/screen`,
returns both**, recomputed from the same preferences — change 领域 or 目标人物 and
both lists move. Nothing is precomputed; a stored shortlist is stale the moment a
preference changes.

**The method is target-first, and the targets are people you cannot buy.**
A client picks 名人 — Elon Musk, Sam Altman, a16z and Sequoia GPs, Karpathy,
LeCun. They are the *audience*. What is purchasable is **the accounts those
famous people follow**, so the primary discovery source is
`目标人物 → 关注 → 账号` from `follow_edges`, collected by
[collect_target_following.py](signal_map/scripts/collect_target_following.py).
That single edge is both the discovery basis and the path-3 relation evidence.

`roster_centrality` (accounts **our own** creators follow) is a *fallback* source
and is off by default once targets are chosen. It answers a different question
and, as §10a already recorded, surfaces mutual-follow pods. The two live in
different `discovery_basis.source` values and a surface may never render the
second as "目标人物关注了他".

Four readouts per candidate, **never merged into a score** — 适配程度 · 关系证据 ·
商务成熟度 · 证据完整度 — feeding four queues: 优先询价 / 先确认合作意愿 /
优先补充商务路径 / 仅作目标或观察. Two rules that look like bugs and are not:
**联系方式不能抵消内容不相关** (a domain mismatch drops to observe regardless of
how good the contact is), and **缺少联系方式不删人** (that is what
优先补充商务路径 exists for). There is deliberately no 0–100 fit score: without a
brief, no unified project ranking may be produced.

Commercial evidence is **three separate facts** of increasing strength, and one
piece of evidence maps to exactly one: 有联系路径 / 过去承接过第三方合作 /
已确认愿意承接本项目. The third can only come from a real reply and is refused
unless recorded `verified` with an actor. 自我推广 (own course/community, and
"DM for collabs") is recorded, shown, and **supports none of them** — see §10a.

External lists (the boss's iLands sample, the outreach workbook) are ingested by
[ingest_bd_candidates.py](signal_map/scripts/ingest_bd_candidates.py) as *a
source*, never as the pool. Their score/tier/notes land in `source_*` columns
permanently flagged `unverified` and are read by no rule. `ilands_fit` is one
client's taste, not a product rule.

取得报价 → `POST .../quotes` writes a **new** quote (never overwrites), keeps the
raw text, and puts the amount in `internal_cost_usd` only. The creator then
enters the client pool automatically — that is the 待BD → 已有报价 seam.

## 6. Internal vs client isolation — built

[client_safe.py](signal_map/backend/client_safe.py) + [app.py](signal_map/backend/app.py). One database does not mean one permission set. **Filtering happens in the backend serializer, never in the frontend.**

Internal only — must never appear in any client API response: 供应商底价 · Mango 加价/利润 · 私人联系方式 · 供应商身份 · 谈判记录 · outreach 记录 · path-1 关系数据 · 内部可触达等级 · 内部备注 · 未验证信息 · 其他客户的反馈 · 风险调查 · 拒绝原因 · 研究来源.

Client-safe: public profile · cleared client price · Mango's curated reason · publishable content/audience data · displayable Signals · risk notices · that client's own project and feedback · Mango's final recommendation.

Serializers are **allowlist-based** — every client payload is built field by field, so a new column is invisible to clients until someone names it there. A blocklist is a promise to remember every future column, and that promise breaks.

Two namespaces: `/api/client/*` (per-client bearer token, scoped to the caller — there is deliberately **no `client_id` parameter**, or a caller could request someone else's data) and `/api/internal/*` (separate token, **fails closed** when unconfigured). Another client's resource returns 404, not 403 — a 403 confirms the id exists.

Both routers share **one** session dependency (`db.session_dependency`). Two dependencies would mean a test could override one and leave the other pointed at the real database — an isolation test passing while the endpoint it guards is untested.

### Mango's operating surface

[internal_api.py](signal_map/backend/internal_api.py) closes the loop that was leaking: a client Approve raised a 询价 task, and those tasks had nowhere to go. It carries the work queue, the quote review queue (**with the verbatim source text** — the parse is what is under review), per-brief pipeline with contacts and cost, and a data-coverage health view.

Two separations that matter:

- **Review and pricing are different calls.** Judging *what the creator charges* and deciding *what to charge the client* are different decisions by potentially different people; collapsing them would let a parse fix silently publish a price.
- **Clearing a quote for display stays a human act.** `client_price_usd` is only ever set by an explicit call carrying an `actor`, a quote still flagged `needs_review` cannot be cleared, and a client price below recorded cost is refused rather than absorbed.

Correcting an amount **re-derives the client band** — a stale band is what a client would otherwise see. Doing the work **closes the task** that recorded it, or the queue only ever grows.

The guard is applied at `include_router`, not per route, and a test enumerates every internal path from the OpenAPI schema and asserts 401 — so a route added later is covered without anyone remembering to.

### Prose leaks past field allowlists

The hardest leak class here, and one that actually reached generated samples before being caught: `internal_cost_usd` was rendered **inside generated sentences** — the budget axis, `reasons`, and `matched` all formatted it as `"$1,300"`. No field-name allowlist sees that. The first leak test missed it too, because it searched for `"1300"` while the string read `"$1,300"`.

Fix: `AxisResult` carries `client_detail` beside the internal `detail`, and every client-serialised list uses `detail_for_client`. Guarded by a test that pulls real costs from the DB and checks **every rendering** of each number. Any new generated string embedding a figure must do the same.

Contract for the frontend: [signal_map/docs/API.md](signal_map/docs/API.md). Live sample payloads, regenerated from the running code: [signal_map/docs/api_samples/](signal_map/docs/api_samples/).

## 7. Lovable frontend

Backend first. Do **not** build the final client frontend — the user will supply a Lovable project.

Prepare stable data protocols for: 项目/简报 · 筛选项 · 客户偏好 · 推荐批次 · 推荐对象 · KOL 详情 · 报价 · 预算汇总 · 候选名单 · 比较 · 反馈事件 · 缺失和错误状态. Ship a **desensitized example response set generated from real data** for Lovable to integrate against.

When Lovable arrives: understand its client experience → map pages to endpoints → find missing fields and semantic conflicts → prefer an adapter layer → extend the schema only if needed. Never force the client UI to mirror the internal DB shape; never let a temporary page shape cap the long-term vision. **Audit every "路径" the design shows and confirm which of the four it means.**

## 8. Wording is binding

[product-language.md](kol_database/docs/product-language.md) governs all copy. One concept, one name, product-wide; if a screen disagrees with the doc, fix the screen.

- Three judgments stay independent, never merged into one badge: **Opportunity / Relationship / Execution**.
- E1–E3 are public-data inferences. Only E4+ (requires a real logged human confirmation) may be called 已验证 warm path.
- Fact-status vocabulary: 已验证事实 / 高概率推断 / 研究线索 / 未知 / 已过期，需重新核实.
- Banned: "已确认" without saying what · "真实路径" over a one-way follow · "技术上可触达" · "最佳 connector" without showing the comparison.
- Connector selection is never by follower count and never silently auto-picked.
- Missing data shows 未知. A model never fills it in. A *rule* may derive a
  coarse bucket from real evidence, but only under the three constraints in
  §3 — stated basis, separate column from measured data, and never excluding
  unknowns from results.

Prior research (finance KOLs, AI KOLs, Grok seed, Dov's HTML method, Apify tests, relationship samples) is **not discarded** — it belongs in a research/candidate layer carrying source, collected-at, subject, signal type, direction, raw evidence, limitations, confidence, and whether a human confirmed it. Starting point for the future Root graph; **not** a production relationship database today.

## 9. Never do this round

Create a second creator / company / contact / quote master · build the client frontend · fabricate KOLs, quotes, case studies or relationships · unbounded candidate expansion · Apify bulk scraping · contact any KOL, manager or supplier · auto-send email or messages · expose internal cost · generate fake business data for demo effect.

## 10. Running and testing

```bash
# rebuild Signal Map's database from the BD archive (read-only on kol.db)
.venv/bin/python -m signal_map.scripts.migrate_from_bd            # rebuild
.venv/bin/python -m signal_map.scripts.migrate_from_bd --dry-run  # report only

# then resolve 待确认 creators from live content (cached; --limit caps cost)
.venv/bin/python -m signal_map.scripts.enrich_youtube_apify     # YouTube, via Apify
.venv/bin/python -m signal_map.scripts.enrich_unknown_profiles  # X, via Rapid X

# then re-judge audience with the LLM (covers ALL priced creators, not just gaps)
.venv/bin/python -m signal_map.scripts.classify_audience_llm

.venv/bin/python -m signal_map.scripts.classify_roots_llm      # suggest Root types (never decides)
.venv/bin/python -m signal_map.scripts.generate_api_samples    # refresh Lovable samples

# schema change to the observation layer -- create_all adds tables, not columns
.venv/bin/python -m signal_map.scripts.add_root_review_columns
.venv/bin/python -m signal_map.scripts.add_bd_candidate_tables

# 可 BD 筛选：先采目标人物关注了谁（方法主线），再把外部名单去重接进来
.venv/bin/python -m signal_map.scripts.collect_target_following --from-candidates
.venv/bin/python -m signal_map.scripts.ingest_bd_candidates --all --dry-run

uvicorn signal_map.backend.app:app --reload --port 8100        # run the API
.venv/bin/python -m pytest signal_map/tests/ -q

# old BD system (reference / data inspection only)
uvicorn kol_database.backend.app:app --reload --port 8000
sqlite3 kol_database/data/kol.db
```

The migration is **idempotent and re-runnable**: it opens `kol.db` read-only,
drops and rebuilds Signal Map's tables, and re-parses quotes from preserved raw
text. Quote parsing will keep improving, so never hand-edit parsed quote rows —
fix [quotes.py](signal_map/backend/quotes.py) and re-run.

Because it *drops* the tables, a rebuild also discards enrichment results —
always re-run the enricher after it. That is cheap: both the Rapid X and
channel-page caches are consulted first, so a re-run costs no API quota.

`DB_PATH` overrides the DB location (Railway uses a mounted volume; without it a redeploy loses every write). Old deploy notes: [kol_database/docs/railway-internal-release.md](kol_database/docs/railway-internal-release.md).

Required tests for Signal Map: permission tests (**no internal field escapes the client API**) · recommendation logic · quote parsing · budget math · feedback persistence.

## 10a. Operational lessons worth not relearning

Each of these cost real time or shipped a wrong result before being caught.

- **A graph result of exactly zero everywhere is a suspected join bug** until
  the id spaces are proven to match. The migration dropped `x_rest_id`, so
  roster accounts sat under `handle:xxx` placeholders while follow endpoints
  return numeric ids. Every intersection compared strings that could never
  match and returned a clean, entirely false zero — across leaders, bridges
  *and* products. Now: `platform_uid`, and the collector **skips loudly**
  rather than inventing a placeholder.
- **Never run two writers against the SQLite file.** A stage-3 collection died
  mid-run because an LLM pass was writing concurrently. `busy_timeout` is now
  60s with WAL, but the real rule is: run one writer at a time.
- **Prose leaks past field allowlists**, and by subtraction too. `internal_cost_usd`
  reached generated sentences twice — once in the budget axis, once in a
  scenario note that gave the percentage of a visible budget. Both are guarded
  now; **every new client endpoint must be added to the leak scan**, which is
  how the scenarios leak survived its first review.
- **Editing a prompt must invalidate its cache.** The verdict cache keys on
  content + model + prompt fingerprint. It did not at first, so a prompt fix
  silently had no effect on already-cached creators.
- **An observation is per-look, not per-run.** `observation_count` incremented
  on every script run including cache reads, which quietly turns one follow
  into a "pattern".
- **Test the contract, not a number.** Tests asserting "12 axes" broke when a
  13th was added as designed. They assert `len(AXES)` now.
- **A ranking metric answers the question it was built for, not the one you
  now want.** `audience_concentration` was designed to beat raw follower count
  and it does — but it maximises for "this account's audience is unusually
  our roster", which describes a mutual-follow pod as accurately as a Root. It
  put @MagnaDing (15K followers, follows 84 of Mango's 188 creators) above
  @AndrewYNg. Selecting candidates and *judging* them need different measures.
- **One ratio has a blind side; compute both directions.** Share-of-following
  caught the pods with small follow lists and cleared the single most
  roster-saturated account in the queue, because it follows 4,000 accounts.
  Whenever a flag is `A/B`, ask what `A/C` says before trusting it.
- **`ruff --fix` deletes `# noqa` for rules that are not in the active
  ruleset.** Running it with a narrower `--select` than the file was written
  against silently stripped `# noqa: E402` from two scripts whose imports
  genuinely follow a `sys.path` setup. Check `--select E402` after any autofix.
- **`Base.metadata.drop_all()` drops the observation layer too.**
  `observation_models` registers on the same `Base`, so "rebuild the Signal Map
  tables" silently included 252,305 follow edges and 4,888 attention signals —
  ~30,000 API calls that `kol.db` cannot re-derive. The migration now drops
  only `_MIGRATED_TABLES` and a test asserts the unrecoverable ones are not in
  it. A rebuild also must not delete a *client's* briefs or feedback.
- **"The ids happen to line up" is not a foreign key.** `creators.id` matched
  BD's for all 357 rows only because BD's sequence is gapless and insertion
  order agreed. One deleted BD row would have repointed every attention signal
  at the wrong creator. The migration now carries BD's PK across explicitly and
  runs `verify_referential_integrity()`, failing the run rather than reporting
  success over a broken graph.
- **A parser placeholder is not data.** `deliverable_raw="(unlabelled)"`
  reached a client card as `$500 以下（(unlabelled)）`. A column holding what
  someone *wrote* must be NULL when they wrote nothing, and each reader
  supplies its own 待确认 wording.
- **Discovering candidates from the wrong end of the graph reads as working.**
  The BD candidate pool was first built from `roster_centrality` — accounts
  *Mango's own creators* follow. It returned 1,452 candidates and looked
  healthy. It was answering "who does our circle watch", when the question is
  "who do the client's targets watch". Same table, opposite direction, and no
  error anywhere. The two now live in different `discovery_basis.source`
  values, and only one of them may be shown as relation evidence.
- **A bio phrase can mean the opposite of what it looks like.** Matching
  "for collabs" as a brand-acceptance signal put eight mutual-follow growth
  accounts at the top of 优先询价 — "DM for Collabs" is the signature of
  accounts that *trade* exposure, which `root_review` already flagged. A
  growth-service marker now **withdraws** the acceptance reading rather than
  sitting beside it. Marker lists for commercial intent must be narrowed to
  phrases that only mean "I take brand money".
- **Peel wrappers in a loop, not by a fixed key order.** The Rapid X user
  payload nests as `result → data → user → result`; a one-pass peel over
  `("result","data","user")` consumes `result` early and stops at the outer
  layer. It reported 43 of 62 targets as "not found" — all of which exist —
  and the run exited 0.
- **`expire_on_commit=False` makes a write-then-read handler lie.** Writing a
  quote and immediately re-rendering the card returned 尚无报价: the already
  loaded `creator.quotes` collection is not refreshed on commit. Only the
  write→read path is affected, and the database is correct throughout.
- **A FastAPI route function called directly gets `Query(...)` sentinels.**
  Write handlers reusing a GET handler to render their response passed the
  sentinel objects downstream. Endpoint bodies that other code needs belong in
  a plain helper, with the route as a thin wrapper.

## 11. Next phase (design for it, don't build it)

Root and 关系 Signal as an added recommendation axis · 商业采购路径 modeling (经理人/Agency/MCN/供应商 rosters, minimum spend, commission, exclusivity, usage rights) · supplier network · 投放与转化路径 after campaigns run · **AI FrontRun** sharing the same people/orgs/Roots/content while keeping a separate client surface.

Signal Map asks: 客户希望影响这些人和圈层，应该通过哪些 KOL、媒体和商务路径进入？
AI FrontRun asks: Mango 长期观察的重要人物，最近开始关注和互动了哪些账号、项目与趋势？

**FrontRun's delta layer is built** — [frontrun.py](signal_map/backend/frontrun.py),
`/api/internal/bd/frontrun/*`, internal only. It exists because collecting the
107 targets' following lists for Signal Map happened to build the substrate:
the observation layer was designed for change detection from the start.

The whole correctness story is one guard. `first_seen_at` means **Mango first
observed this edge**, not "the follow began then" — X does not expose the
latter. So an observer with no earlier **complete** snapshot contributes
nothing: without that rule, a first collection prints "Elon just followed 1,405
accounts", which is false and embarrassing. A truncated or errored snapshot is
likewise not a baseline (a missing edge there may just be an unread page), and
an unfollow is only claimed when **both** snapshots are complete. When there is
no baseline the answer is the *status*, never an empty list — an empty list
reads as "nothing happened" when the truth is "we cannot tell yet".

First real output, over a 2-day gap on 8 watchers: @karpathy → @paulfchristiano
(Alignment Research Center), @simonw → @EvanHub (Anthropic alignment lead),
@jeremyphoward → @tianyi (DeepSeek), plus 5 unfollows. `converged` (newly
followed by ≥2 watchers in one window) sorts first: one person following
someone is routine, two important people doing it in the same week is not.

Shared data layer, never a shared client interface. Roots a client picks in Signal Map can feed AI FrontRun's watchlist; accounts AI FrontRun discovers can flow back as new leads after verification.
