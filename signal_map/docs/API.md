# Mango Signal Map — Client API

For the Lovable frontend. Every endpoint below is exercised by
[`generate_api_samples.py`](../scripts/generate_api_samples.py), and the real
responses it produces live in [`api_samples/`](api_samples/) — build against
those files, not against this prose, because they are regenerated from the
running code.

```bash
.venv/bin/python -m signal_map.scripts.generate_api_samples   # refresh samples
uvicorn signal_map.backend.app:app --reload --port 8100       # run the API
```

## Auth

```
Authorization: Bearer <client token>
```

One token per client, stored on `clients.api_token`. **Every request is scoped
to the client the token resolves to** — there is deliberately no `client_id`
parameter anywhere in the client API, because a caller could then ask for
someone else's data. Another client's brief returns `404`, not `403`: a 403
would confirm the id exists.

`/api/internal/*` uses a separate `SIGNAL_MAP_INTERNAL_TOKEN` and is **not**
reachable with a client token. It fails closed — unset means unavailable, not
open.

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/client/filters` | Filter options that actually match inventory |
| POST | `/api/client/briefs` | Create a brief |
| GET | `/api/client/briefs` | List this client's briefs |
| GET · PUT | `/api/client/briefs/{id}` | Read / update a brief |
| POST | `/api/client/briefs/{id}/recommendations?limit=30` | Run a batch |
| GET | `/api/client/runs/{id}` | Re-read a batch |
| GET | `/api/client/creators/{id}` | Creator card |
| GET · POST | `/api/client/briefs/{id}/candidates` | 候选名单 + budget |
| DELETE | `/api/client/briefs/{id}/candidates/{creator_id}` | Remove (soft) |
| GET · POST | `/api/client/briefs/{id}/feedback` | Approve / Reject / Question |
| GET | `/api/client/briefs/{id}/scenarios` | 组合方案与预算比较 |
| GET | `/api/client/scenarios/meta` | Scenario types and what each optimises for |

`/api/client/filters` only offers values present on priced, recommendable
inventory — a filter that always returns nothing is worse than no filter.

## What the client never receives

Not filtered in the frontend, and not filtered by blocklist: every client
payload is **built field by field** in
[`client_safe.py`](../backend/client_safe.py). A new column on any model is
invisible to clients until someone names it there.

Never present: Mango's cost or margin · private contact details · supplier or
manager identity · negotiation records · outreach logs · path-1 商务触达 data
(who at Mango knows whom) · internal reachability grades · internal notes ·
another client's anything · `rank_score`.

### Prices: band, not figure

The stored quote is what the creator asked **Mango** for — Mango's cost. It is
never sent to a client. Until Mango sets a markup, the client sees a band:

```json
"quote": {
  "price_exact_usd": null,
  "price_band": "$500–1,000",
  "price_label": "$500–1,000",
  "status_label": "报价已确认"
}
```

`price_exact_usd` is populated only once Mango sets a client price; until then
render `price_label`. A budget total behaves the same way — `total_usd` is
`null` and `total_label` reads `价格待 Mango 确认` rather than substituting the
internal cost total.

This applies to generated prose too, not just fields: axis details, `reasons`
and `matched` all carry the band. (The internal surface reads exact figures
from a different field on the same records.)

## Recommendations

Twelve axes, judged independently, **all twelve always returned**:

```
内容与行业 · 市场与语言 · 平台 · 内容形式 · 目标人群 · 传播目标
预算可执行性 · 报价确定程度 · 商业可执行性 · 历史合作 · 风险 · 数据可信度
```

Each is `{key, label, verdict, detail}` where verdict is one of:

| verdict | meaning | UI |
|---|---|---|
| `match` | meets the preference | ✓ |
| `partial` | meets it in part | ~ |
| `mismatch` | conflicts with it | ✗ but **still shown** |
| `unknown` | Mango has no data | 待确认 |
| `not_asked` | brief didn't state one | hide or grey |

Alongside: `matched`, `unmet`, `missing`, `reasons`, `risks`, `next_step`.

Three behaviours the UI must not "fix":

- **`unknown` never removes a creator.** Missing data lowers confidence, it
  does not disprove fit. Render 待确认.
- **`mismatch` never removes one either.** The client is entitled to see what
  Mango considered and why it ranked low. Show it with the miss visible.
- **Never show a bare score.** `rank_score` is not in the payload at all. Rank
  order is the only ordering signal exposed, and it must appear beside the
  axes that produced it.

`unsupported_filters` lists preferences Mango could not apply. Surface it —
a filter that silently does nothing is worse than one that says so.

## Feedback → Mango's work queue

`POST .../feedback` with `action` ∈ `approve` `reject` `question` `shortlist`
`unshortlist`. Persisted per 客户 + 项目 + 对象, append-only: a later approve
does not erase an earlier reject, because the sequence is itself signal.

Server-side effects:

| action | effect |
|---|---|
| `approve` / `shortlist` | added to 候选名单; raises a 询价 task if no client price, and a 商务路径 task if no contact |
| `reject` / `unshortlist` | removed **from this brief only** — never downgrades the creator in the supply layer, and never affects another client's project |
| `question` | becomes an internal action item carrying the client's text |

## 组合方案 (scenarios)

Five portfolios from one brief: 专业可信度 / 圈层覆盖 / 产品转化 / 中英文跨境 /
头部与垂直混合. All five are built from the **same** assessments — only the
selection strategy differs, so a scenario disagreeing about a creator is a
difference of purpose, not of fact.

Each carries `thesis`, `tradeoff`, `picks`, `coverage`, `budget`, `risks`,
`alternates`, `notes`. Render the **tradeoff as prominently as the picks** — a
scenario shown without what it gives up is a recommendation pretending to be a
choice.

Per pick, `adds` is why it is in the portfolio: the audiences, markets, formats
and Roots it contributed that the set did not already have. Selection is greedy
on marginal coverage, not on rank, so the list order is a build order.

`coverage.audience_overlap` (0–1) is reported deliberately, not optimised away:
some repetition is intentional and the client should see how much they are
buying. A scenario that could not be assembled is still returned, carrying the
reason in `notes` — a missing option is information.

## KOL–Root 注意力路径 (axis 13)

Built and partial: follow signals from Roots to creators, plus repost evidence
where it exists. Every grade below 已验证事实 is an inference, so the axis
never reads `match`.

| what was observed | wording |
|---|---|
| follow only | 关注信号，非互动证据 |
| 1 interaction | 历史熟悉度信号，非已验证关系 |
| 2+ recent interactions | 高概率推断 |
| nothing | 关系数据待补充 |

A follow proves a follow existed at observation time. Not endorsement, not
exposure, not timing. X does not expose when a follow began, so **never render
recency for a follow**. Empty is 当前未观察到 — do not substitute any other
relationship data for it.

### 已确认 Root vs 候选账号 — render the difference

The axis detail names which one it is, and the frontend must not flatten them:

| stored state | detail reads |
|---|---|
| a human accepted the account, with a type | `被已确认 Root（投资人）@x 关注…` |
| nobody has reviewed it yet | `被候选账号（尚未人工确认为 Root）@x 关注…` |
| a human rejected it | the signal is **absent** — it never reaches the axis |

This is not cosmetic. 249 accounts follow at least one of Mango's creators, and
a first machine pass suggests **~73% are not Roots at all** — mostly content
creators inside the roster's own mutual-follow network. Showing an unreviewed
candidate as a Root would put a fabricated attention path in front of a client.

When accepted Roots and unreviewed candidates both exist for one creator, the
sentence is built from the accepted ones only and ends with
`；另有 N 条来自未审核候选，未计入`.

## Internal surface (`/api/internal/*`, separate token)

Not for the client frontend — listed so the boundary is visible.

| Path | Purpose |
|---|---|
| `GET /tasks` · `POST /tasks` · `PATCH /tasks/{id}` | Mango's work queue |
| `GET /tasks/summary` | Queue depth by kind, oldest open |
| `GET /quotes/review-queue` | Quotes blocked from display, with source text |
| `PATCH /quotes/{id}/review` | Human verdict on a parse |
| `PATCH /quotes/{id}/pricing` | Set client price / clear for display |
| `GET /briefs/{id}/pipeline` | Candidates + contacts + cost + open tasks |
| `GET /coverage` | Data-gap counts |
| `GET /roots/summary` | Review progress; how much of the Root layer a person stands behind |
| `GET /roots/review-queue` | Root candidates with named flags and a machine suggestion |
| `GET /roots/{handle}` | One candidate, plus how the ranking surfaced it |
| `PATCH /roots/{handle}` | **The only writer of `root_type`.** Accept/reject, actor required |

The review queue returns **flags, not a score**. The ranking metric that
produced the candidate list cannot separate a genuine industry figure from a
mutual-follow peer: @AlfaizAliX beats @AndrewYNg on listed-count rate, and both
sit in the same follower band. Two arithmetic flags do separate them —
`roster_sweep` (what share of Mango's 188 collected creators this account
follows) and `peer_network` (what share of its own follow list is Mango's
roster) — and neither subsumes the other, so both are reported.

`root_suggested_type` and `root_type` are different columns and never merge.
A model writes the first; only `PATCH /roots/{handle}` with an `actor` writes
the second. Accepting requires a real type — `unknown` is refused, because an
accepted Root that cannot say which 圈层 it reaches answers nothing.

## Not yet available
- Exact client prices (pending a markup policy).
- 投放与转化结果 — only exists after a campaign runs.
