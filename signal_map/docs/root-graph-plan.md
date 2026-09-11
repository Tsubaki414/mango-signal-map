# Plan — KOL–Root 注意力路径 (path 3)

Status: **designed, not built.** `attention_signals` has 0 rows.

Answers: *这个 KOL 会把内容送进谁的信息流？被哪些高价值的人关注？*

---

## 0. What exists today, and what it is not

The repo already holds a 105,102-edge X graph (`outputs/pilot_v4/mango_bd_v4.sqlite`).
**It cannot answer this question.** Its 80 collected accounts are Mango's own
connectors, and the only account collected as a "root" is `Solomon_Nahhh` —
Mango's own person. It is **path 1** (how Mango reaches a target), a different
question about different people.

Coverage of the sellable inventory:

| | |
|---|---|
| priced creators with an X account | 192 |
| whose own follow graph was collected | **0** |
| appearing anywhere in that graph, even as a node | **1** |

So this is a build from zero, not an extension. Do not reuse those edges.

## 1. Direction is the whole design

> To know whose feed a creator enters, the edge needed is **Root follows KOL**.
> `KOL follows Root` says only that the KOL can see the Root. It is not the same fact.

Getting this backwards is the easiest way to build something that looks right
and answers nothing.

Collection cost follows from direction, and the asymmetry is large:

| approach | API calls |
|---|---|
| pull **followers** of all 192 KOLs | **29,928** (14.9M followers ÷ 500/page) |
| pull **following** of 300 Roots | **~600** |

A Root's *following* list is small (hundreds to a few thousand); a KOL's
*follower* list is huge. Same edge, 50× the cost from the wrong end.

Machinery already exists — `src/mangobd/rapid_x.py` has
`get_following_ids(username, count=500, cursor)`, `get_follower_ids(...)`, and
`get_users_by_ids(...)` for batch ID→profile resolution, all cache-first.
Nothing needs to be written from scratch, and per AGENTS.md Rapid X is the only
sanctioned X source.

## 2. The needle-in-a-haystack problem

The naive product flow fails:

```
客户点名 20 个大 V  →  查我们的 262 人够不够得着  →  多半全是 0
```

There are far more notable accounts than our roster touches, so an unconstrained
Root picker mostly returns nothing, and the feature looks broken rather than
honest.

**Invert it.** Precompute a standing Root universe once, then:

- the client picks from **Roots our roster demonstrably reaches** — never zero;
- the client may still name any account, which we fetch on demand and report
  honestly, *including* 当前未观察到.

That turns a haystack into a menu, without ever hiding a real zero.

## 3. Two-stage collection

The trick is that stage 1 both **generates** the Root universe and **ranks**
what is worth verifying in stage 2 — at a fraction of stage 2's cost.

### Stage 1 — who does our own roster pay attention to? (~400 calls)

Fetch the **following** list of each of our 192 priced X creators. Count how
many of them follow each account.

```
account followed by 60 of our 192 KOLs  →  centre of gravity of this circle
```

This is cheap (following lists are small) and gives a **data-driven Root
candidate set** grounded in the vertical our inventory actually lives in —
rather than a hand-guessed list of famous people our roster has no relation to.

⚠️ Stage 1 alone proves nothing about feed entry. `KOL follows Root` is the
wrong direction. It is a **candidate generator and a priority ranking**, and
must never be presented as an attention path.

### Stage 2 — does the Root follow back? (~600 calls for 300 Roots)

For the top candidates, fetch **their** following lists and intersect with our
192 KOLs. Every hit is a real path-3 edge: `Root → follows → KOL`.

### Stage 3 — interaction evidence (per pair, on demand)

A follow is the weakest signal there is. Upgrade the strongest pairs with
reply / quote / mention evidence via `get_user_tweets` — the existing
`collect_x_interactions_v4.py` logic already does this shape of work.

Per [product-language.md](../../kol_database/docs/product-language.md):

| evidence | status | may be called |
|---|---|---|
| Root follows KOL | 研究线索 | "存在关注 Signal" |
| 1 interaction | 历史熟悉度信号 | "曾有公开互动" |
| 2+ interactions, both directions, ~18 months | 高概率推断 | "近期多次双向互动" |
| a human confirmed it | 已验证事实 | — |

Never: "会看到"、"认可"、"触达概率". Zero results is **当前未观察到**, never
两者无关系.

## 4. Root typing — and why it varies by client

A "大佬" is not one thing, and the right ones change per client. Type each Root:

| type | what it is | who buys it |
|---|---|---|
| 行业大佬 | cross-vertical recognition, very large following | 品牌认知 / 融资叙事 |
| 垂直专家 | smaller, deep authority in one vertical | 专业背书 |
| 投资人 | VC / angel / fund | 融资叙事 |
| 媒体节点 | journalist, newsletter, publication | 品牌认知 |
| 社区领袖 | runs a community, not just a feed | 圈层渗透 |
| 企业决策者 | CTO / VP / buyer-side | B2B 转化 |

Reuse the existing `AUDIENCE_TYPES` vocabulary rather than inventing a parallel
one — a Root's type and a creator's audience type are the same axis seen from
two ends, and keeping them aligned is what lets "this creator reaches investors"
and "this Root is an investor" join up.

Relevance is then **per client**, driven off the brief: an AI dev-tools launch
wants developers + CTOs + AI investors; a consumer app wants a different set
entirely. Root selection should default from `Brief.verticals` +
`Brief.target_audiences` + `Brief.objectives`, and stay overridable.

## 5. Schema

`AttentionSignal` already exists and is deliberately separate from every path-1
table. It needs no redesign — only population — but two additions are worth
making first:

- a `Root` table: identity, type, vertical, why-it-matters, curated-by,
  follower count, and **whether Mango or the client proposed it**;
- `RootSignalRun`, so a re-collection is a new observation rather than an
  overwrite — "Root followed this KOL in Sept" must stay true after an unfollow.

`AttentionSignal` already carries direction, occurred-at, observation count,
raw evidence, collected-at, coverage limitation, confidence, human-confirmed,
and conflict-of-interest. Fill them all; a signal missing its direction or its
collection date is not usable evidence.

## 6. Honest limits, stated up front

- A follow observed today says nothing about **when** it started. X does not
  expose that. Never imply recency from a follow.
- Coverage is bounded by what was collected — record `coverage_limitation` on
  every row, and never let absence read as disproof.
- No exposure or conversion data exists at this layer. Path 4 is a different
  question and does not become answerable by collecting more follows.
- Employment, ownership and paid relationships disqualify a signal as
  independent attention — `conflict_of_interest` exists for this and must be
  checked before a Root–KOL pair is shown as evidence.

## 6a. Stage 1 results (collected 2026-09-08)

195 snapshots · 252,305 edges · 166,643 accounts · 28,033 ranked candidates.
157 complete, 29 truncated at the page cap, 3 failed.

### Raw count and pure concentration both fail

| metric | top result | why it is wrong |
|---|---|---|
| roster followers | `@elonmusk` — 119/192 (62%) | 241M global followers; the follow carries no information |
| concentration alone | `@RiverCmkt` — 95 global followers | ~95-follower accounts in a mutual-growth pod, not Roots |

Usable signal sits in a **band**. Ranking accounts with 10K–2M global
followers by `audience_concentration` gives `@tavus`, `@withneo`, `@trymirage`,
`@ImagineArt_X`, `@BytePlusGlobal`, `@TopviewAIhq`, `@DoraTool`.

### What the roster can actually reach

Those are **AI product and startup accounts**, not thought leaders — which
follows directly from what this roster is: AI-tool review and demo creators
follow the tools they review.

So the reachable Root layer is the **AI product ecosystem**, and it is reachable
well: our roster is ~0.4% of the entire audience of accounts like `@tavus`.

### The 行业大佬 layer *is* partly reachable

⚠️ An earlier version of this section concluded the opposite. That was a
**measurement bug, not a finding**: the migration dropped `x_rest_id`, so every
roster account was stored under a `handle:xxx` placeholder while the follow
endpoints return numeric ids. Every intersection compared strings that could
never match, and returned a clean, entirely false zero. Fixed by
`SocialAccount.platform_uid`; the collector now **skips** an account with no
real id rather than inventing a placeholder for it.

Re-measured with real ids:

| leader | follows our creators |
|---|---|
| `minchoi` | **18** — @TheTuringPost, @rohanpaul_ai, @BenGeskin … |
| `alexmashrabov` | **5** — @rohanpaul_ai, @nrqa__, @LinusEkenstam … |
| `AndrewYNg` | 1 — @ai_for_success |
| `demishassabis` | 1 — @ai_for_success |
| `ylecun` | 1 — @BrianRoemmele |
| `Scobleizer` | 1 — @aryanlabde |
| `karpathy`, `sama`, `gdb`, `nikitabier` | 0 |

`@ai_for_success` is followed by **both Andrew Ng and Demis Hassabis**.
`@LinusEkenstam` appears in four different AI product accounts' following lists.

AI product accounts → our creators: **87 edges** across 10 products
(TopviewAIhq 17, PixVerse 17, DoraTool 16, magnific 14, tavus 6 …).

So real path-3 signal exists and is nameable. The very top tier (`karpathy`,
`sama`) is still not reached, but "the leader layer is closed" is false.

**Shared attention remains high and is still not a path.** These leaders'
following lists overlap our roster's attention by 34% (`minchoi` 74%,
`demishassabis` 71%, `sama` 58%). Per [AGENTS.md](../../AGENTS.md) a V-shape —
both sides following the same third account — is **not** routable reachability
and must never be shown as one. Use the named direct hits above instead; they
are true, checkable, and stronger.

**Lesson worth keeping:** a graph result of exactly zero everywhere should be
treated as a suspected join bug until the id spaces are proven to match. It was
only caught by asking why *fourteen* product accounts all returned zero.

## 6b. Stage 2 review results (2026-09-08) — 73% of the candidates are not Roots

Stage 2 produced **249 accounts with at least one real `Root → creator` edge**,
4,888 follow signals and 52 interaction signals. Step 3 of §7 said human review
gates everything after it; the review surface now exists
([root_review.py](../backend/root_review.py), `GET /api/internal/roots/*`), and
running the first machine pass over the queue produced this:

| suggestion | count |
|---|---|
| **非 Root（疑为同类创作者）** | **181** |
| 项目/产品账号 | 47 |
| 行业大佬 | 8 |
| 投资人 | 5 |
| 垂直专家 | 4 |
| 媒体节点 · 社区领袖 · 企业决策者 | 4 |

**The candidate ranking surfaced the roster's own peer network, not Roots.**
The metric that drove it — `audience_concentration` — maximises for exactly the
wrong thing: an account whose audience is disproportionately Mango's roster is,
in the common case, a mutual-follow pod member.

The arithmetic that separates them, once both directions are computed:

| account | follows our creators | of its follow list | of our 188-creator roster |
|---|---|---|---|
| @Trisha_Techie | 71 | **8.9%** | 37.8% |
| @MagnaDing | 84 | 2.1% | **44.7%** |
| @AndrewYNg | 1 | 0.09% | 0.5% |
| @garrytan | 5 | 0.08% | 2.7% |

Both ratios are needed and neither subsumes the other. @MagnaDing is the most
roster-saturated account in the entire queue and its share-of-following is a
harmless-looking 2.1%, because it follows 3,988 accounts — the first version of
the flag cleared it as "selective". A small follow list dense with our creators
and a large one that swept the roster wholesale are the same behaviour seen
from two ends.

**No single score would have worked.** @AlfaizAliX (a pod account) beats
@AndrewYNg on listed-count-per-follower, 21.3 vs 11.0, and they sit in adjacent
follower bands. Any composite would have ranked the pod above the laureate and
shown one confident number for it.

After flags + suggestion, **60 of 249 carry no negative mark** — real Roots
(@AndrewYNg, @garrytan, @demishassabis, @ClementDelangue, @AravSrinivas,
@amasad) and AI product accounts (@Kling_ai, @HeyGen, @magnific, @pika_labs,
@MiniMax_AI, @replicate). That is the substantiated version of §6a's "the
reachable Root layer is the AI product ecosystem".

Still true and still binding: **these are suggestions.** `root_type` is written
only by `PATCH /api/internal/roots/{handle}` with a named actor. Until a person
rules on an account, the axis says 候选账号（尚未人工确认为 Root）— never Root.

## 7. Sequence

1. ✅ `Root` + `RootSignalRun` schema, and a Root-typing vocabulary aligned to `AUDIENCE_TYPES`.
2. ✅ Stage 1 collection (~400 calls) → Root candidate set ranked by roster centrality.
3. ✅ Stage 2 collection → the real `Root → KOL` edges (249 accounts, 4,888 signals).
4. ✅ Recommendation axis #13, added the same way as the others: separate verdict, own evidence, `unknown` never eliminates.
5. ✅ Review **surface** — flags, machine suggestion, accept/reject with an actor (§6b).
6. ⏳ **Human review of the 249** — 0 done. This is where Mango's judgment enters, and it gates everything after it.
7. ⏳ Client surface: only verified, desensitized signals; 关系数据待补充 when empty.
8. ⏳ Stage 3 interaction upgrade for the strongest pairs (52 signals so far, ~1% of edges).

Steps 3 and 4 originally ran in the other order. Collecting before review was
the right call on cost — the edges are cheap and re-collectable — but it means
the axis has been live on **unreviewed** candidates, which is why the axis now
says 候选账号 rather than Root until step 6 reaches an account.

## 8. What this must not become

- Path-1 data (who at Mango knows whom) rendered as a client-facing relationship graph.
- A "被看到概率" derived from follows.
- A Root list of famous accounts our roster has no observed relation to, shown as if it were coverage.
- A reason for dropping creators with no Root signal — absent data lowers confidence, it never eliminates a candidate.
