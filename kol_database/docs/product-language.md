# Product Language Guide

This is the single source of truth for wording across the Mango Growth & BD
Intelligence Cockpit. If a screen's text doesn't match this document, the
document wins — fix the screen, don't add a new synonym.

The point of this file is that every concept has exactly ONE primary name,
site-wide. When you're about to write a new label or generate a new piece
of copy, check here first.

## Three independent judgments — never merge these

The product makes three separate judgments about every company. They are
computed independently and none of them gates the others. A screen must
never collapse two of these into one badge or one sentence.

1. **Business Priority** (`商业优先级`) — pure commercial value: spend
   evidence, paid sponsorship history, GTM fit, timing. Says nothing about
   whether Mango can currently reach anyone there.
2. **Relationship Stage** (`关系阶段`, E0–E6) — how warm the relationship
   actually is, backed by evidence. Says nothing about commercial value.
3. **Execution Status** (`执行状态`) — what's actually actionable today,
   derived from the two judgments above plus whether a real path/operator/
   offer exists. This is the only one of the three that should ever drive
   a "do this now" sort order.

## Relationship Stage (E0–E6)

| Code | Name | What it takes to earn it |
|---|---|---|
| E0 | 无关系线索 | Nothing on file |
| E1 | 仅关注/互关 | A one-way follow or an unchecked/zero-interaction mutual-follow bridge |
| E2 | 历史熟悉度信号 | One public interaction found (reply/mention/quote), or a shared event |
| E3 | 近期多次双向互动 | 2+ interactions, both directions, within ~18 months |
| E4 | 已验证 warm path | A real human (Mango-side) confirmed they actually know the target — **requires an OutreachLog row, never inferred from public data** |
| E5 | 连接人同意引荐 | The connector agreed to introduce — **requires an OutreachLog row** |
| E6 | 引荐已发生并获得回应 | Intro was sent AND the target replied — **requires an OutreachLog row** |

Hard rules:
- E1–E3 must **never** be described as "真实关系", "已验证", "warm intro",
  or anything implying a human confirmed it. They are public-data
  inferences only.
- Only E4+ may use the phrase "已验证 warm path".
- Only E5 may say "连接人同意引荐".
- Only E6 may say "引荐已发生并获得目标方回应".
- A one-way follow is always "研究线索" (research lead), never a "path"
  or "relationship" on its own.
- A mutual-follow bridge with no interaction evidence is always "引荐候选
  人，关系未核实" (introduction candidate, relationship unverified) — never
  "connector" alone, which implies a settled role.
- A single public interaction is "历史熟悉度信号" (a historical
  familiarity signal) — informative, but explicitly not proof of a real
  relationship.
- Connector selection is never by follower count. If evidence doesn't
  distinguish between candidates, show the comparison and say so — never
  silently pick one.
- Only an explicit `relationship_rejected` record (the person says they do
  not know the target) disproves a candidate relationship. `no_response`
  and a declined request are execution outcomes; neither proves that the
  underlying relationship is false.

## Contact verification fields (per operator/contact)

A contact card must show these as **independent, separately-labeled
facts** — never collapse them into one "已确认" badge:

- 身份是否人工核实
- 当前职位是否人工核实
- X 账号是否匹配
- 私信开放状态是否核实
- 公开邮箱/表单是否核实
- 是否可能掌握或影响预算
- 与 Mango 的关系是否核实
- 最后核实日期

"私信已验证开放" is a fact about the account. It is never rendered as, or
folded into, "联系人已确认" — identity verification and DM-openness are
different questions with different evidence.

公开资料双源匹配是只读的 research-pipeline fact。普通联系人编辑不能把
它写成 true；姓名、职位、X handle 或证据来源被修改后，旧匹配自动失效，
等待重新研究。身份、职位和预算影响力的人工核实分别记录时间，勾选时必须
附可回查 evidence URL。一次普通编辑绝不自动核实任何字段。

Site-wide rename: **"已确认联系人" → "已识别目标联系人"** (we identified
them as the target contact; that's different from having verified their
identity).

## Execution Status

| Label | Meaning |
|---|---|
| 现在联系 | High-value + a real usable path exists today |
| 本周准备 | Worth pursuing but needs prep (proposal, or path not yet usable) |
| 等待内部核实 | Blocked on someone at Mango checking a candidate |
| 等待回复 | Contact made, no response yet |
| 需要跟进 | A follow-up is due |
| 暂缓 | Not worth acting on right now |
| 放弃 | Explicitly closed out |

## Fact-status vocabulary (use these exact words)

- 已验证事实 (verified fact) — a human confirmed it, or it's a directly
  observed API result (e.g. "私信已验证开放").
- 高概率推断 (high-confidence inference) — strong but not human-confirmed
  (e.g. E3's recent+repeated+bidirectional interaction pattern).
- 研究线索 (research lead) — one-way data, unconfirmed either direction.
- 未知 (unknown) — no data at all.
- 已过期，需重新核实 (stale, needs re-verification) — old research
  content whose claim is no longer trustworthy on its own (e.g. "内部整理
  线索" ConnectorBrief rows).

## Action-plan structure

The suggested next step for a company starts from one of five route
shapes, numbered 1/2/3 based on what actually exists — never a fixed
A/B/C, never a step shown without the step before it, never "同时"
(also/in parallel) language implying something that isn't there:

1. Warm candidate(s) + direct channel → ①核实暖路径 ②并行直接联系 ③fallback
2. Only a direct channel → ①直接联系 ②fallback
3. Only unverified connector candidate(s) → ①内部核实 connector ②核实失败后的 fallback
4. Nothing at all → ①找正确 operator ②准备具体 offer ③找新 connector 或官方渠道
5. Low/medium evidence and no channel → ①补齐商业/负责人证据 ②记录可执行 fallback 或暂缓条件

Once a real OutreachLog exists, its latest non-void event overrides the
route's first-touch wording. A contacted target becomes 等待回复/需要跟进;
a booked meeting becomes 准备会议; a requested proposal becomes 制作并发送
提案. Never show “现在去私信” beside “等待回复”.

## Sponsorship evidence language

- `paid_sponsorship + unreviewed` = **付费观察 · 待审核**，不是已确认赞助。
- Only `paid_sponsorship + confirmed` may be called **已复核付费赞助证据**.
- “多次付费合作方” requires at least two confirmed paid rows for the same
  company. Affiliate, mention, rejected, and unreviewed rows do not qualify.

## Forbidden phrases (do not write these)

- "已确认" without saying what was confirmed
- "真实路径" when the underlying data is a one-way follow
- "技术上可触达" (a stand-in for "we can send a cold message" dressed up
  to sound like reachability)
- "最佳 connector" without showing the comparison that produced it
- "商业价值高，所以现在联系" (Business Priority alone never implies
  Execution Status)
- "有 sponsorship，所以不是冷启动" (a past sponsorship is a commercial
  signal, not a relationship)
- "同时"/"并行"/"warm path" appearing when the thing they refer to isn't
  actually present on screen

## General rules for any auto-generated copy

- One sentence, one judgment.
- Facts and recommendations are visually/textually separate.
- Evidence and inference are labeled differently (see fact-status
  vocabulary above).
- Current state and historical record are separate — a stale
  ConnectorBrief note is never presented at the same confidence level as
  a live Relationship Stage.
- The primary action goes first, not buried after paragraphs of context.
- Don't repeat information already visible elsewhere on the same screen.
- No filler paragraphs without an action or a fact in them.
- English terms (E0–E6, connector, warm path, etc.) are fine, but must be
  explained in Chinese on first appearance in any given view.
