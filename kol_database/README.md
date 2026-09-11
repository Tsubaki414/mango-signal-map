# Mango Growth & BD Intelligence Cockpit

One app, one database, four modules: **Opportunities** (which AI companies
to chase, ranked by an explainable priority), **Network** (who at Mango/Solomon
may be worth asking, based on public relationship hypotheses and an internal
verification queue), **Creators** (the creator quote/contact-record library;
recorded contact details do not prove Mango has already built a relationship),
and **Campaign Builder** (turn an Opportunity into a budgeted
shortlist). It replaces two previously separate tools -- the KOL quote
filterer and the Solomon Action Map / AI-company-x-KOL evidence pipeline --
which now share one data model instead of living in two disconnected
databases. See [§9](#9-growth--bd-intelligence-integration) for how the merge
works and what's real vs. still a known gap.

Core Creators flow: **import, enrich (Rapid X + ScrapeCreators + GPT), filter/compare, shortlist, export.**
Core Opportunities flow: **audit a company's reachability + spend evidence, find the intro path, build a campaign, staff it with suggested creators.**

### Visual design

The UI is a dark theme built from mangolabsglobal.com's actual brand,
verified against the live site (computed styles + pixel-sampled colors),
not guessed: near-black surfaces (`rgb(19,20,23)`), warm off-white text
(`rgb(244,246,241)`), the brand's vivid orange-red accent sampled from the
hero image (`rgb(251,67,32)`), `font-bold italic` on the wordmark, sharp
(near-zero radius) buttons, uppercase tracked labels, system-ui sans
throughout -- all things the marketing site actually does. What did **not**
carry over: atmospheric photography, gradients, and hero-scale type, none
of which belong in a dense data table. Status badges stay pill-shaped
(a documented exception to matching the brand's sharp corners everywhere
else -- pills are the functional convention for scanning tags at a glance).

### Creator-source snapshot (2026-08-26 base quote-library enrichment run)

301 creators were imported from the two quote-library sources. Enrichment
coverage for that base cohort:

| Platform | Enriched | Adapter |
|---|---|---|
| X | 219/219 | Rapid X |
| Instagram | 22/22 | ScrapeCreators |
| YouTube | 29/52 | ScrapeCreators (credit-exhausted, see below) |
| TikTok | 4/5 | ScrapeCreators |
| LinkedIn / Threads / Facebook | 0/1 each | no adapter (see §8) |

(Failures within each platform are real accounts that appear
suspended/renamed/deleted, not tool bugs -- see `enrich_all.py`'s error
output for the exact handles.)

Classification result: **149 Strategic KOLs**, **120 KOC / Distribution**,
**32 Needs Review**. Of the 269 classified creators: 188 High confidence,
81 Medium, **0 Low** -- every classified creator now has at least
Medium-confidence reasoning; the ones genuinely too thin to call stay in
Needs Review rather than getting a forced guess.

The current unified internal-UAT database additionally contains 39 accounts
created from sourced BD sponsorship evidence. The live Creator total is
therefore **340**: **183 Strategic**, **120 KOC / Distribution**, **4 Media /
Community**, and **33 Needs Review**. Media/community properties are isolated
from people/creator inventory instead of being counted as core KOLs. This
distinction prevents the older 301-row enrichment cohort from being mistaken
for the full cockpit inventory.

**A full reclassification pass found and fixed two real bugs**, not just
re-ran the same logic:
1. **Every Instagram creator was defaulting to Unknown** even with a real
   bio, follower count, and engagement rate, because Instagram's API
   doesn't expose post captions and the prompt was treating "no post text"
   as "no signal" -- it's actually a structural gap in one field, not an
   absence of evidence. Fixed the prompt to classify from bio + metrics
   when post text is unavailable (see §6). This alone moved 20 Instagram
   creators out of Needs Review.
2. **Mango's own outreach-team notes were never sent to the classifier.**
   One creator's sheet name literally said "(agency-run AI virtual
   influencer, not a real person)" and the internal notes flagged a 272.6%
   views/follower ratio "consistent with its AI virtual persona / paid
   traffic tactics" -- the outreach team had already caught this, but the
   classifier had no access to that note and called it a "KOL" anyway.
   Fixed: `internal_notes` and the original sheet display name (when it
   differs from the scraped one) now go into the prompt, with an explicit
   instruction to treat human due-diligence as higher-trust than the
   classifier's own read of the bio. Re-running caught it correctly:
   "Marketing Account: 由 Valid 广告代理运营的AI虚拟网红账号，粉丝与浏览比异常高" (High confidence).
   A repo-wide scan for creators with similarly flagged internal notes
   found 3 total; the other 2 were already correctly classified.
3. Also fixed: the classification cache key didn't include the prompt
   text itself, so improving the prompt (fix #1/#2 above) would have kept
   silently serving old verdicts reasoned under the old rules forever.
   Cache key now includes the full system prompt.

**YouTube is credit-exhausted, not blocked**: ScrapeCreators is a
credit-metered API (see §5.3), not the same account as Surf (a separate,
crypto-market-data tool used by the unrelated BD-intelligence pipeline in
this repo -- topping up Surf does not add ScrapeCreators credits, they're
different services). 23 YouTube creators remain unenriched; re-run
`python3 kol_database/scripts/enrich_all.py --platform youtube` once more
ScrapeCreators credits are on the account (`--limit N` to control spend;
2 credits/creator).

## 1. What's in scope, what isn't

Implemented:
- Import from the two existing KOL_data sheets (quoted rows only), with
  idempotent re-import (matches/merges by platform+handle) and platform-name
  normalization (`X(Twitter)` -> `X`, `未在邮件中注明` -> `Unknown`, long
  multi-platform descriptions -> `Multi-platform`, with the original text
  preserved for provenance).
- A parsed, structured rate-card table per creator, alongside the verbatim
  quote text (nothing is thrown away, and nothing numeric is guessed).
  Handles k/m-suffixed amounts (`£6k` = 6,000 GBP), ranges, mixed
  currencies, and several "glued together, no separator" list formats.
- Four mutually exclusive views: **Strategic KOLs**, **KOC / Distribution
  Inventory**, **Media / Community**, and **Needs Review**. A person only ever
  appears in Strategic or KOC/Distribution once classified with real
  evidence; media/community properties have their own lane, while
  unclassified or low-signal accounts sit in Needs Review rather than being
  defaulted into Strategic.
- Full filter set (checkboxes, not an unbounded pill wall), sortable
  directory table with real avatars (initials fallback when no avatar URL
  is available, and on image load failure), platform badges, Estimated CPM.
- Creator detail drawer: profile, classification + reason + confidence +
  source, all platforms, deliverables with per-deliverable CPM, contacts,
  recent posts, promoted-project mentions, internal notes.
- Manual classification/edits that are **locked** against being overwritten
  by a later auto-classification run.
- Rapid X enrichment (X/Twitter): profile + recent posts -> avatar, bio,
  avg/median views, engagement rate, posting frequency, original/repost
  ratio, heuristic promo ratio, recent promoted-project mentions.
- YouTube Data API v3 enrichment (adapter built, gated on `YOUTUBE_API_KEY`
  -- see §5.2): channel thumbnail/title/description/subscribers/country,
  recent uploads -> avg/median views, engagement, posting frequency,
  heuristic sponsor-mention extraction.
- GPT-assisted classification (creator class, reason, confidence,
  promotion level, categories, region/language guess, one-line content
  summary), cached, never overwrites a manual edit, and explicitly answers
  "Unknown" rather than guessing when evidence is thin.
- Shortlists: create/switch/rename, add/remove creators, pick which
  deliverable to price, live budget math (KOL spend / KOC spend / total /
  remaining), CSV/XLSX export, "copy for Google Sheets" (TSV to clipboard).

The original quote-library v1 did not include company/operator research,
relationship graphs, or outreach logging; those capabilities now live in the
Growth & BD integration described in §9 and §11. Still explicitly out of
scope: automated email/Telegram sending, payments/contracts, fake-follower
modeling, a black-box creator score, and conversion prediction.

## 2. Setup

From the repo root:

```bash
python3 -m venv .venv               # or reuse an existing one
source .venv/bin/activate
pip install -r kol_database/requirements.txt
```

For screenshot/browser UAT tooling, install the separate development set and
its browser binary (these are intentionally not baked into production):

```bash
pip install -r kol_database/requirements-dev.txt
playwright install chromium
```

### Environment variables

This reuses the repo-root `.env` (already gitignored, `chmod 600`) instead
of keeping a second copy of credentials:

```
RAPID_X_API_KEY=...          # already present in this repo's .env
RAPID_X_HOST=twitter241.p.rapidapi.com
OPENAI_API_KEY=...           # for GPT-assisted classification
OPENAI_MODEL=gpt-4o-mini     # optional override
SCRAPECREATORS_API_KEY=...   # for YouTube/Instagram/TikTok enrichment -- present, credit-limited (see §5.2)
YOUTUBE_API_KEY=...          # optional alternative to ScrapeCreators for YouTube specifically -- not provided
APIFY_TOKEN=...              # bounded official-web / LinkedIn / YouTube / public-route candidate research
INTERNAL_ACCESS_TOKEN=...    # required for writes; blank keeps the app fail-closed/read-only
```

`RAPIDAPI_KEY` is also accepted as an alternate name for the Rapid X key.
**No key is ever written into source, logs, cache payloads, or the
frontend.** If a required key is missing, the relevant enrich action is
disabled in the UI and the API returns a 400 telling you what to set,
before making any request.

### Apify candidate-research workflow

Apify is a discovery and evidence-candidate layer, not an auto-confirmation
engine. Actor inputs are cost-bounded; raw output stays under the gitignored
`data/cache/apify/` tree; normalized observations enter
`data/pilot_v5/apify_research_review_queue.json` as `observed_unreviewed`.
LinkedIn is used only for current public operator/job candidates, never for
Mango relationship-degree claims.

```bash
python3 scripts/collect_apify_intelligence.py health
python3 scripts/collect_apify_intelligence.py collect --source official_web \
  --company-id company:gamma --company-name Gamma --url https://careers.gamma.app/growth-marketing-manager --dry-run
python3 scripts/collect_apify_intelligence.py refresh-runs
python3 scripts/build_apify_candidate_exports.py
python3 kol_database/scripts/apply_apify_review_candidates.py             # rollback dry-run
python3 kol_database/scripts/apply_apify_review_candidates.py --apply     # still imports as unreviewed
```

The deployable `kol_database/data/apify_company_intelligence_v1.json` is a
small allow-listed public-evidence package. It excludes raw Actor payloads,
cache paths, run/dataset IDs and credentials. Company detail and Campaign
Preview preserve candidate status, unresolved geography and missing pricing
instead of turning a scrape result into a confirmed business fact.

## 3. Data import

Source files (already in `KOL_data/`, not modified by this tool):

- `KOL_data/建联名单总表 - Fiona-英文AI KOL copy.csv` -- only rows where
  `有报价 == 1` are imported (209 rows). All of these link to `x.com`, so
  platform is fixed to X.
- `KOL_data/KOL回复报价表.xlsx`, sheet `回复报价` -- every row already has a
  quote (93 rows), spanning YouTube/Instagram/X/TikTok/etc.

Run (idempotent -- safe to re-run after editing the source sheets; matches
existing creators by `(platform, handle)` from the profile URL and updates
them instead of duplicating):

```bash
python3 kol_database/scripts/import_data.py
```

If you get a **new** sheet later with different columns, the loader
functions in `kol_database/backend/importer.py`
(`load_csv_records` / `load_xlsx_records`) are the place to add a mapping
for the new column names -- the DB schema and the rest of the pipeline
don't need to change.

### Quote-text parsing

The `报价` / quote columns are free-text written by dozens of different
people. `backend/parsing.py` does best-effort structured extraction into
`(deliverable, amount, currency)` rows, but **every** row keeps
`is_confident` and the verbatim `raw_quote_text`:

- Segments it can confidently parse become a normal rate-card row.
- Segments with numbers but no price cue (a currency symbol/word, a k/m
  multiplier, or a `:`/`per`/`for` cue) are **not** guessed into a price --
  they show up as `is_confident=False`, quote fields empty, with the
  original text visible in the detail drawer.
- This deliberately errs toward leaving something blank over inventing a
  number a Mango BD person might act on.

### Currency conversion

`backend/fx.py` is a small static, manually-maintained USD reference table
(set at build time, 2026-08) -- there is no live FX API in scope. If a
quote isn't in USD, the original amount+currency is always kept alongside
the USD-converted figure (shown in the UI as `$X (Y GBP)` etc.).

## 4. Running the app

```bash
uvicorn backend.app:app --app-dir kol_database --reload --port 8811
```

Then open `http://127.0.0.1:8811/`. The FastAPI app serves both the JSON
API (`/api/*`) and the static frontend (`kol_database/frontend/`, plain
HTML/CSS/JS, no build step, no framework) from the same process.

### Bulk enrichment (all creators at once)

The UI's per-page "Refresh data" button and per-creator drawer button are
for day-to-day use. To enrich the whole database in one pass (what this
build actually ran):

```bash
python3 kol_database/scripts/enrich_all.py --platform x        # Rapid X, all X creators
python3 kol_database/scripts/enrich_all.py --platform youtube  # once YOUTUBE_API_KEY is set
```

Cache-first (`data/cache/rapidx/`, `data/cache/youtube/`, `data/cache/gpt/`),
so re-running only refreshes what's missing or past its TTL. This is a
slow operation dominated by Rapid X's rate limit (roughly 10-15s per
creator between the profile call, the tweets call, and GPT classification)
-- expect ~40-50 minutes for the full X creator set. Run it in the
background and check progress via its per-10-creator log lines.

## 5. Enrichment sources

### 5.1 Rapid X (X/Twitter)

Uses `twitter241.p.rapidapi.com` via `backend/rapidx_client.py`, a
cache-first client (default TTL 6h) so repeat page loads don't burn quota.
Endpoints used: `user` (profile) and `user-tweets` (recent posts) --
confirmed against this repo's actual Rapid X account (field names such as
`legacy.favorite_count`, `views.count`, `legacy.retweeted_status_result`
for repost detection, and the `TimelinePinEntry` instruction for the
pinned tweet) rather than assumed from docs.

Averaging method (`backend/enrichment.py`): pulls the most recent posts,
**excludes the pinned tweet** (curated, not representative) and splits the
rest into original vs. repost. Over original posts with a view count,
`avg_views` is a **trimmed mean** (drops the single highest and single
lowest value when there are >=5 eligible posts) so one viral or one dead
post doesn't swing the number; `median_views` is stored alongside as a
second, even-more-outlier-resistant reference point. Engagement rate =
(avg likes + avg replies + avg reposts) / avg views over that same trimmed
set. Fewer than 5 eligible posts is flagged (`enrichment_error: "low
sample: ..."`) rather than silently presented as a solid number.

### 5.2 ScrapeCreators (YouTube / Instagram / TikTok) -- live and verified

`backend/scrapecreators_client.py` (cache-first, same pattern as Rapid X)
and `backend/scrapecreators_enrichment.py` cover YouTube, Instagram, and
TikTok through the unified ScrapeCreators API
(`https://api.scrapecreators.com`, header `x-api-key`). Every endpoint's
field mapping below was **confirmed against a live response** at build
time, not just the docs page -- the docs summary for TikTok, for instance,
describes `stats` as nested under `user.stats`; the real response has it as
a top-level sibling key, and the code follows the real shape.

- **Instagram** (`v1/instagram/profile`, 1 credit/creator): profile +
  recent posts in a single call. Only video/reel posts carry a view count
  (`video_view_count`) -- photo posts have real likes/comments but no view
  field at all. Averaging likes across all posts while averaging views
  across only the video subset produced a real bug during testing (a
  viral photo's likes divided by a much smaller video-view average, giving
  a >100% "engagement rate"). Fixed: Instagram engagement rate is computed
  against **followers**, the industry-standard approach for exactly this
  reason, using every post's likes/comments rather than only the video
  subset. Post captions are not included in this endpoint, so
  `promotional_content_ratio` is left blank for Instagram rather than
  guessed from view/like counts alone.
- **YouTube** (`v1/youtube/channel` + `v1/youtube/channel-videos`, 2
  credits/creator): channel profile, then up to 30 recent uploads with
  `includeExtras=true` for like/comment counts. Same trimmed-mean averaging
  as every other adapter, same heuristic sponsor-mention extraction from
  video descriptions as the (unused) official-API adapter below.
- **TikTok** (`v1/tiktok/profile` + `v3/tiktok/profile/videos`, 2
  credits/creator): profile/stats, then recent videos with play/like/
  comment/share counts.

**ScrapeCreators is credit-metered, not flat-rate** -- the account you gave
me carries roughly a 100-credit starter balance, which does not cover a
run of this size in one pass. See the data snapshot above for exactly what
was spent and what's left to enrich. `enrich_all.py` does not check the
remaining balance before starting; use `--limit` to spend deliberately.

An alternative, unused-for-now YouTube path also exists:
`backend/youtube_client.py` / `backend/youtube_enrichment.py` implement the
**official** YouTube Data API v3 (`channels.list`, `playlistItems.list`,
`videos.list`) against the documented contract, but were never exercised
against a live key (none was provided). `app.py`'s enrich dispatch prefers
ScrapeCreators for YouTube whenever `SCRAPECREATORS_API_KEY` is set (it
is), and falls back to this official adapter only if you later add a
`YOUTUBE_API_KEY` instead -- useful if you'd rather use Google's free
10,000-units/day quota for YouTube specifically and save ScrapeCreators
credits for Instagram/TikTok.

Until enriched, YouTube/Instagram/TikTok creators sit in **Needs Review**
with whatever the source sheet already had (name, follower count, quote,
profile link) -- the honest state rather than a guessed one.

`Estimated CPM = quote_usd / avg_views * 1000`, per deliverable, on every
platform; blank whenever `avg_views` isn't available -- never a fabricated
estimate.

## 6. GPT-assisted classification

`backend/classify.py` calls the OpenAI Chat Completions API directly (no
SDK dependency, same lightweight `urllib` pattern as the rest of this
repo's Rapid X client) with the account's platform, bio, metrics, a
handful of recent post excerpts, **and** `creator.internal_notes` plus the
sheet's original display name when it carries the outreach team's own
annotation (e.g. flagging an agency-run account) -- human due-diligence is
explicitly instructed to outweigh the model's own read of the bio/metrics.
Asks for a JSON object: `creator_class`, `creator_class_reason` (short,
concrete), `classification_confidence`, `promotion_level`, `categories`,
`region`, `language`, `content_summary`. Cached 30 days per
(model, full system prompt, user prompt) hash -- the system prompt is
part of the cache key on purpose, so editing the classification rules
invalidates old verdicts instead of silently continuing to serve results
reasoned under the previous rules. **Never overwrites a creator whose
classification was manually edited** (`creator_class_locked`) -- manual
edits win, permanently, until a human changes them again.

The prompt explicitly instructs the model to answer `Unknown` (routed to
the Needs Review tab, not Strategic) when there isn't enough signal -- but
"no post captions available" is treated as a platform limitation, not
missing evidence: Instagram's API never returns captions, so a real bio +
follower count + engagement rate alone is enough for a `Medium`-confidence
call. `Unknown` is reserved for when even the bio and metrics are thin.

## 7. Testing

```bash
cd kol_database
PYTHONPATH=. python3 -m unittest discover -s tests -v
```

The full suite covers quote-text parsing (annotated-follower-cell regression,
k/m-multiplier regression, glued-numbered-list regression), platform-name
normalization, container-segment URL handles (`/channel/UC.../`, `/in/`,
`/c/` regression), tab bucketing (Unknown -> Needs Review, not Strategic),
FX conversion, CPM/quote-summary math, Rapid X stat computation
(pinned/repost exclusion, trimmed mean, low-sample flagging), the
Instagram photo-vs-video engagement-rate regression, GPT-result cleaning
(the literal-string-"null" regression), a full import run against the real
`KOL_data/` sheets, and (`test_bd_compute.py`) the Opportunity/reachability/
campaign-fit/relationship-strength scoring logic, relationship-stage
language, lossless follow-direction serialization, dynamic outreach
continuations, cross-page state consistency, operator verification,
sponsorship review status, UAT-data cleanup, reconciliation and the
advisory GPT review packet. The dated UAT report records the exact count
from the release-candidate run so this README cannot silently go stale as
new regressions are added.

## 8. Known limitations

- **27 of 52 YouTube creators are not yet enriched** -- ScrapeCreators
  credit balance ran out partway through (see the data snapshot at the top
  and §5.2). Re-run `enrich_all.py --platform youtube` once more credits
  are on the account.
- **LinkedIn, Threads, Facebook (1 creator each) have no adapter built.**
  ScrapeCreators does technically cover all three, but with one creator
  apiece it wasn't worth the engineering time this round -- they keep
  whatever the source sheet had plus the profile link, and sit in Needs
  Review. Adding them would follow the exact same pattern as
  `scrapecreators_enrichment.py`'s Instagram/TikTok functions if it becomes
  worth it later (more creators on those platforms, or a specific
  campaign need).
- Quote-text parsing is best-effort on genuinely inconsistent free text; a
  minority of rows (numbered lists with literally zero whitespace before
  the next number) can still misparse. The raw text is always shown next
  to the parsed row so this is always checkable.
- FX table is a static snapshot, not a live rate feed -- fine for internal
  budgeting, not for invoicing.
- The directory filters/sorts in Python over an eager-loaded creator list
  rather than pushing every filter into SQL. At the current data size
  (hundreds of creators) this is simpler and still fast; if the DB grows
  into the tens of thousands of creators this would need to move back into
  SQL.
- `promotional_content_ratio` and sponsor-mention extraction are
  keyword/pattern heuristics on top of raw post/description text, not a
  judged signal -- treat them as a rough flag, not a score.
- Region/language are genuinely unavailable from X's API (it doesn't
  expose either) and only come through when GPT can infer them from bio
  text, or when YouTube's channel `country`/`defaultLanguage` is set by the
  channel owner (often blank). Most X-only creators will show no
  region/language, correctly, rather than a guess.

## 9. Growth & BD Intelligence integration

This app used to be two separate tools: the KOL quote filterer above, and a
Solomon Action Map / AI-company-x-KOL sponsorship-evidence pipeline that
lived in `outputs/pilot_v4/mango_bd_v4.sqlite` with its own briefing doc
(`outputs/pilot_v4/SOLOMON_KOL_COCKPIT.md`). They now share one SQLite
database and one FastAPI app. The merge was a real data migration, not a
UI reskin -- see below for what moved and how the two systems now
cross-reference each other.

### 9.1 What migrated

`scripts/migrate_bd_data.py` reads `outputs/pilot_v4/mango_bd_v4.sqlite`
(read-only, untouched) and writes into `kol_database`'s own tables:
`Company`, `CompanyAlias`, `CompanySource`, `Operator`, `IntroPath`,
`ActionItem`, `SponsorshipEvidence`, `GtmCase`, `ConnectorBrief` (see
`backend/models.py`'s BD-intelligence section for the full schema, and the
script's own docstring for the source-table mapping). It is idempotent and
upserts by stable source keys; it does not delete/reinsert human-editable
rows. Existing imported rows and local rows survive refreshes, while new
canonical rows are added without duplication (full preservation rules are
in §11.3). Current internal-UAT snapshot:

| Entity | Count |
|---|---|
| Companies | 89 (77 canonical + 12 sourced local extensions) |
| Company aliases | 4 |
| Company sources (evidence URLs) | 107 |
| Operators (named people) | 29 identified; 0 human-verified |
| Intro paths | 640 raw rows; unevaluated placeholders do not count as relationship evidence |
| Action items | 42 |
| Sponsorship evidence rows | 64 (42 paid observations, all still awaiting human review) |
| GTM cases | 8 |
| Connector briefings | 5 archived research notes; excluded from live stage/priority |

Entity resolution matches on `(platform, handle)` case-insensitively. A
sponsor mentioned in the BD evidence who already exists in the Creators
quote library (e.g. `daniel-dann`) is linked to that same row -- one
person, one `creator_id`, visible from both Creators and Opportunities.
A sponsor with no prior quote-library row gets a new `Creator` with
`creator_class="Unknown"` (Needs Review) and zero rate cards, which is the
correct representation of "known from research, never quoted."

### 9.2 The four modules

- **Home** (`/api/home/summary`) -- answers, every load: the next three to
  five executable actions, the exact operator/channel, owner and follow-up
  date, plus the internal connector questions that can unlock several
  companies. It uses the live action transition generated from the latest
  non-void outreach event; it does not repeat a stale first-touch action.
- **Opportunities** (`/api/companies`, `/api/companies/{id}`) -- filterable,
  sortable company list; company detail shows reachability paths, named
  operators, action items, and every sponsorship-evidence row with its
  disclosure type (paid / affiliate / ambassador / event / mention /
  unknown -- never auto-promoted to "paid").
- **Network** (`/api/network/interview-queue`, `/api/network/operators`) --
  an execution queue grouped by the Mango-side person to ask, with concise
  company-specific questions and live evidence stages. The five older
  ConnectorBrief notes remain available only as collapsed, potentially
  stale research material and never drive the current stage or priority.
- **Campaign Builder** -- not a new entity. "Build a campaign" on a Company
  page calls `POST /api/companies/{id}/campaigns`, which creates a
  `Shortlist` row with `company_id` set plus objective/audience/timing --
  the existing Shortlist model, budget math, and CSV/XLSX/TSV export all
  keep working unchanged. The shortlist modal then shows a "Suggested
  creators for this company" panel (`/api/companies/{id}/suggested-creators`,
  explainable reasons only: prior relationship, category overlap, quote on
  file -- no opaque score) so staffing the campaign from evidence takes one
  click per creator.

### 9.3 Cross-module links (the part that makes this one app, not two)

Every one of these is a real click target backed by a foreign key, not
static text:

- **Creator -> Company**: a creator's drawer shows "Sponsorship history"
  (`sponsorship_history` in `GET /api/creators/{id}`) with every company
  they've been evidenced promoting; clicking a company name opens that
  Company's Opportunities detail.
- **Company -> Creator**: a company's sponsorship-evidence list links back
  to the matched Creator (when resolved) with a `has_quote` flag, so you
  immediately see whether outreach can start from an existing quote or
  needs one first.
- **Company -> Person**: identified operators are listed on the company
  page with source match, human identity check, current-role check,
  budget-influence check, exact X profile, DM status and Mango relationship
  shown as separate facts.
- **Network -> Company / Opportunity -> Campaign**: connector-brief company
  pills and the "Build a campaign" button both route into the same
  Opportunities/Shortlist objects, not a copy.
- **Global search** (`/api/search`) spans Companies, Operators, and
  Creators from one input in the topbar, and opens the right drawer
  directly.

### 9.4 Scoring (three independent, explainable judgments)

`backend/bd_compute.py` -- every function returns a label plus the plain
`reasons: list[str]` that produced it, never a single opaque number:

- **Business priority** (`opportunity_priority`): whether the company is
  commercially worth pursuing, based on spend/campaign evidence; it does
  not pretend that a valuable company is reachable.
- **Execution priority** (`execution_priority`): what is worth doing now,
  combining business value with a real direct channel, verified warm
  relationship, unverified connector candidate, operator/offer readiness
  and the latest non-void outreach event.
- **Creator campaign fit** (`creator_campaign_fit`): has a Mango quote?
  previously paid-sponsored *this* company (not just mentioned it)?
  category overlap? Each is a separate reason string.
- **Relationship stage** (`relationship_stage`): E0-E3 are public-data
  signals; only attributed human OutreachLog events can advance E4-E6.
  Open DM is a separate cold/direct channel, never a relationship stage.
- **Graph degree / direction** (`relationship_strength`, `IntroPath`):
  direct / secondary / third are graph-hop labels analogous to LinkedIn,
  not LinkedIn lookup. Every available hop retains observed follow
  direction; source gaps are explicitly unavailable and never inferred
  from node order.

### 9.5 Running the migration

```bash
cd kol_database
PYTHONPATH=. python3 scripts/migrate_bd_data.py
```

Safe to re-run any time the source `mango_bd_v4.sqlite` is refreshed with
new BD research -- it will not duplicate companies or creators, **and it
will not clobber human edits** made through the API (see §11.3). Prefer
`scripts/refresh_all.py` over calling this directly (§11.4).

### 9.6 Known limitations specific to the BD side

- **Data sourcing is currently X (Rapid X) + YouTube (ScrapeCreators/YouTube
  Data API) only**, matching the Creators side. If BD research expands to
  need e.g. LinkedIn activity or a paid-search/ads-intelligence source for
  spend evidence, that needs a named API + env var decision before any
  code is written -- see §5 above for the existing adapter pattern to
  follow, and ask before filling a gap with inferred data.
- **`IntroPath.graph_reachable` is a technical-graph fact computed at BD
  research time, not live** -- it does not re-check whether a path is still
  valid today. `human_intro_status` (separately tracked) is the field that
  reflects an actual person's current willingness to introduce.
- **A small number of sponsorship-evidence "creators" are actually outlets,
  not individuals** (e.g. a tech-news publication credited as an affiliate
  sponsor). As of the data-integrity sprint (§11) these are correctly
  classified `Media / Community Account` or `Non-creator / Irrelevant` and
  are excluded from (or, for media accounts, segregated out of) Suggested
  Creators -- see §11.1. A handful of ambiguous individual accounts still
  sit in Needs Review by design (GPT declined to guess from a bare channel
  name and video-title list alone, correctly).
- **Migration's "preserve human edits" rule for `Company` is per-field, not
  per-edit-event** (§11.3): once a field like `category` has any non-empty
  value, a later source correction to that same field will never overwrite
  it automatically. A genuinely-updated source value needs a human to apply
  it manually (or clear the field first) -- this is a deliberate
  safety-over-freshness tradeoff, not an oversight.
- **`Operator`/`IntroPath`/`ActionItem`/`SponsorshipEvidence` upserts are
  create-only once a source row has been seen** (§11.3): if the *source*
  BD research corrects an already-imported row (not a new one), that
  correction won't flow through automatically either, for the same reason.

## 10. Company-first commercial research layer

The current Solomon home view begins with a 97-company commercial longlist and
15 priority dossiers. Commercial scoring is intentionally independent of X
reachability, mutual follows, follower count, company fame, and fundraising
amount. Those signals remain available only as contact-route support.

Each priority dossier carries why-now and spend/GTM evidence, a relevant
operator, a company-specific Mango offer, one best route, two fallbacks, key
unknowns, and atomic public-source records with dates and fact status. The
Opportunities view can filter by Mango ICP and commercial disposition and
defaults to commercial-priority sorting.

The idempotent production apply is:

```bash
python3 scripts/apply_commercial_research_v5.py
```

It is part of `entrypoint.sh`, so existing Railway volumes receive the new
tables and sourced rows without replacing the database or deleting historical
network, action, outreach, quote, or sponsorship records. JSON handoff files
live in `../data/pilot_v5/`.

## 11. Data-integrity + Solomon UAT + internal launch-prep sprint

An incremental sprint on top of the already-stable Creators/Opportunities/
Network/Campaign-Builder flows (§9) -- none of that core plumbing changed.
This section covers what was added: creator re-classification, manual
correction endpoints, the Solomon UAT pilot, and what's needed before the
tool is used by more than one person.

### 11.1 Creator classification taxonomy

`CREATOR_CLASSES` (`backend/models.py`): `Top KOL`, `Community Leader`,
`KOL`, `KOC`, `Marketing Account`, `Media / Community Account`,
`Non-creator / Irrelevant`, `Unknown`. The last two both surface in the
**Needs Review** tab (`NEEDS_REVIEW_CLASSES`), distinguished by badge text:
`Unknown` = not yet resolved, `Non-creator / Irrelevant` = a human/GPT
already looked and decided it isn't a creator at all -- a terminal verdict,
not a pending one.

The 39 creators `migrate_bd_data.py` auto-creates from BD sponsorship
evidence (no bio, no followers, no post history -- only a channel
name/handle and the real sponsorship rows that reference them) get a
grounded, conservative GPT pass:

```bash
cd kol_database
PYTHONPATH=. python3 scripts/classify_bd_creators.py           # apply
PYTHONPATH=. python3 scripts/classify_bd_creators.py --dry-run # preview only
```

See `backend/bd_creator_classify.py`'s system prompt for exactly what
signal this uses (channel name/handle + real sponsorship-evidence titles,
nothing invented) and why it's instructed to prefer `Unknown` over
guessing when that's genuinely all there is to go on. Skips creators that
are no longer `Unknown` or have `creator_class_locked=True` (a human
already classified them manually) -- safe to re-run.

**Suggested Creators filtering** (`bd_compute.rank_suggested_creators`):
`Non-creator / Irrelevant` never appears in any recommendation list.
`Media / Community Account` never appears in the main creator list --
it's returned separately as `media_channels` in
`GET /api/companies/{id}/suggested-creators`, so the frontend can show it
as a distinct "distribution channel" option instead of mixing it into
creator picks. `Unknown` (Needs Review) creators can still surface (e.g. a
real prior sponsor with no classification yet) but always sort after every
confidently-classified result and carry a `needs_review` flag.

### 11.2 Manual correction endpoints

Deliberately minimal -- edit-in-place plus the few "add one more" actions
Solomon actually needs, no approval workflow or generic admin CRUD:

| Action | Endpoint |
|---|---|
| Edit company category/geography/notes, mark verified | `PATCH /api/companies/{id}`, `POST /api/companies/{id}/verify` |
| Edit / add an operator | `PATCH /api/operators/{id}`, `POST /api/companies/{id}/operators` |
| Correct an X intro path | Read-only in the app/API; re-run the Rapid X evidence pipeline so source id, cache provenance and every edge direction are retained |
| Confirm / reject sponsorship evidence | `PATCH /api/sponsorship-evidence/{id}` (`review_status: confirmed\|rejected`) |
| Edit / add an action item (owner, status, due date, outcome notes) | `PATCH /api/action-items/{id}`, `POST /api/companies/{id}/action-items` |
| Record a real outreach event with operator/bridge/path/action attribution | `POST /api/companies/{id}/outreach-logs` |
| Edit an outreach event's operational note, follow-up date or Mango owner | `PATCH /api/outreach-logs/{id}`; provenance/stage fields are immutable |
| Correct/delete a mistaken outreach event without losing history | `PATCH /api/outreach-logs/{id}/void`; no hard-delete endpoint |
| Mark a creator unsuitable for a company/category | `POST/GET/DELETE /api/creators/{id}/exclusions` |

New action items require a nonblank owner, fallback route, exact next action,
and ISO `YYYY-MM-DD` due date; status is limited to the documented workflow
states. Campaign creation requires a nonblank objective, while a lightweight
shortlist may omit one. Budgets and manual quote overrides must be finite and
nonnegative. A selected `rate_card_id` must belong to that shortlist item's
creator; the API rejects cross-creator IDs instead of relying on a bare
foreign key.

Rejected sponsorship evidence is kept (visible, for audit) but excluded
from priority scoring and paid-sponsorship counts (`bd_serializers._non_rejected`,
`bd_compute.opportunity_priority`). Creator detail carries the same
`review_status`: an unreviewed extraction is rendered as “付费观察 · 待审核”,
and “多次已复核付费合作方” only counts confirmed paid rows.

Operator verification is field-specific. Pipeline-owned
`identity_confirmed` (official/company source + exact X profile match) is
read-only to the UI. Human identity, current-role, and budget-authority
checks each require an evidence URL and write separate timestamps;
editing a contact does not verify it. Changing a source-matched name,
role, X handle, or evidence URL invalidates that old source match until a
research refresh rechecks it. `Operator.last_verified_at` is only the
maximum of those explicit field-level timestamps, never a generic edit
timestamp.

All of the above are wired into the Company drawer in the Opportunities
and Network views (inline edit toggles on operators/action items,
Confirm/Reject buttons on each sponsorship-evidence row) and into the
Campaign Builder's Suggested Creators panel ("Not a fit" link).

### 11.3 Migration upsert semantics (why edits survive a refresh)

Before this sprint, `migrate_bd_data.py` deleted and fully re-inserted
every BD table on each run -- fine when nothing was editable, but it
silently wiped any human correction (this was caught mid-sprint: a
`category`/`geography` research pass got clobbered by the next migration
run before this fix landed). Now:

- `Company` is upserted by `company_id`: source-owned fields (name, stage,
  score, priority tier, spend levels, `x_handle`) always refresh from
  source; human-editable fields (`category`, `geography`, `why_now`,
  `budget_evidence`, `buyer_or_route`, `internal_notes`) are only filled in
  when still empty, never overwritten once set. `last_verified_at` is pure
  human state, never touched by migration.
- `Operator`, `IntroPath`, `ActionItem`, `SponsorshipEvidence` each carry a
  `source_id` column (the source system's own stable id for that row).
  Migration upserts by that key: an already-seen row is left completely
  alone (every human-editable field survives); only genuinely new source
  rows get created. Rows a human adds directly via the API have
  `source_id=None` and are never touched by migration at all.
- `add_operator_verification_columns.py` is an idempotent boot migration
  that adds the three field-level human-verification timestamps. It does
  not backfill historical generic edit timestamps into verification facts.
- `reconcile_bd_data.py` (§11.5) has a permanent regression check for this
  exact bug class: duplicate `source_id`s and Company/Operator row-count
  drift both fail the reconciliation.

### 11.4 Solomon UAT pilot

`SolomonReview` (one row per company, keyed by `company_id`) captures a
real decision: `decision` (`proceed`/`watch`/`reject`), what's missing,
free-text notes, and the next action. Relationship progress is not set by
a review checkbox: E4-E6 can only advance from a real `OutreachLog` event.
Selecting a company into the pilot *is* creating this row -- there's no
separate "is_pilot" flag.

`GET /api/solomon/pilot-candidates` buckets real companies into the 4
categories a representative pilot needs (reachable+high-spend,
high-spend-but-weak-reach, has real sponsorship evidence, and a control
case the system says not to act on yet) using the existing
`opportunity_priority()` scoring -- a picker aid, not a selection
mechanism. The Network page shows the current pilot list with each
company's decision status; clicking into a company opens its full review
form at the bottom of the Opportunities drawer (`GET/PUT
/api/solomon/reviews/{company_id}`).

**Current pilot (8 real companies):** Runway, Cursor, Genspark,
ElevenLabs, Synthesia, Gumloop, Manus, Clay -- covering all 4 buckets. Any
UAT-only decision, campaign, shortlist, or relationship-confirmation state
has been removed; these records are waiting for Solomon's real review.
Runway currently has a one-way-follow research lead plus Cristóbal's open
X DM as an independent cold channel. Neither fact is a verified warm path.

### 11.5 Data reconciliation

```bash
cd kol_database
PYTHONPATH=. python3 scripts/reconcile_bd_data.py
```

Checks (machine-readable report at `data/reconciliation_report.json`,
plus a terminal summary): canonical source-key membership plus provenance
for separately researched local extensions, duplicate
company names, duplicate creator handles, orphan rows (sponsorship
evidence / intro paths / action items / operators / Solomon reviews /
creator exclusions pointing at a missing company or creator), duplicate
`source_id`s (§11.3's regression guard), character-by-character-join
corruption in any Text field (the exact shape of the `gtm_motion` bug this
sprint found and fixed), and priced rate cards on unenriched BD-created
creators (would mean a fabricated quote). Exit code 1 if anything fails.

### 11.6 Internal launch-prep

The current handoff is
[`reports/SOLOMON_V8_CAMPAIGN_TRUST_RELEASE.md`](reports/SOLOMON_V8_CAMPAIGN_TRUST_RELEASE.md);
the full historical evidence index remains in
[`reports/SOLOMON_UAT_REPORT_2026-08-29.md`](reports/SOLOMON_UAT_REPORT_2026-08-29.md).

- **Public read / internal write**: the product has no page-level password or
  standalone login UI. GET and HEAD pages, static assets, search, dossiers,
  and other read APIs are public. POST/PUT/PATCH/DELETE always fail closed:
  when `INTERNAL_ACCESS_TOKEN` is absent they return 503, and when it is
  configured they require a short-lived signed browser session, a Bearer
  token, or an `X-Internal-Access-Token` header. The top bar reports `公开只读`
  to an anonymous visitor and `内部编辑已授权` to an authorized session. Full
  deployment/rotation instructions are in
  [`docs/railway-internal-release.md`](docs/railway-internal-release.md).
- **Secrets**: every data/API key (`RAPID_X_API_KEY`, `OPENAI_API_KEY`,
  `SCRAPECREATORS_API_KEY`, `YOUTUBE_API_KEY`) is read only from the
  repo-root `.env` / real environment variables (`backend/env.py`) --
  never hardcoded, never logged.
- **Backup / restore**:
  ```bash
  PYTHONPATH=. python3 scripts/backup_db.py [label] --db "${DB_PATH:-data/kol.db}"
  PYTHONPATH=. python3 scripts/restore_db.py --db "${DB_PATH:-data/kol.db}" --list
  PYTHONPATH=. python3 scripts/restore_db.py --db "${DB_PATH:-data/kol.db}" --yes [--file NAME]
  ```
  Both tools honor the mounted `DB_PATH`; the default backup directory is
  beside that DB under `backups/` (or explicit `--backup-dir`). Backups use
  SQLite's online snapshot API and are integrity-checked. Restore validates
  the source, snapshots the current DB, stages the copy, then atomically
  replaces it. Backups are gitignored and the newest 20 are retained.
- **Release DB contract**:
  ```bash
  PYTHONPATH=. python3 scripts/check_runtime_db.py --db deploy_seed.db --seed-contract
  PYTHONPATH=. python3 scripts/check_runtime_db.py --db "${DB_PATH:-data/kol.db}" --preflight-contract
  PYTHONPATH=. python3 scripts/check_runtime_db.py --db "${DB_PATH:-data/kol.db}" --runtime-contract
  ```
  The Docker build must pass the exact seed contract. On an existing volume,
  startup first runs the preflight contract (integrity/base inventory, while
  allowing columns that migrations add), takes a verified backup, runs the
  idempotent migrations/backfills, and only then requires the full runtime
  contract. It refuses an empty/corrupt/base-incompatible volume rather than
  overwriting it; an old Creator-only Railway volume therefore still requires
  the staged, backed-up migration in the deployment runbook.
- **HTTP and browser release checks**:
  ```bash
  PYTHONPATH=. python3 scripts/run_solomon_uat.py --base-url http://127.0.0.1:8811
  PYTHONPATH=. python3 scripts/capture_uat_screenshots.py --base-url http://127.0.0.1:8811 --strict
  PYTHONPATH=. python3 scripts/run_browser_interaction_smoke.py --base-url http://127.0.0.1:8811
  PYTHONPATH=. python3 scripts/run_release_browser_e2e.py
  ```
  The screenshot command records navigation, API status, console/page errors,
  failed requests, broken same-origin links, external-link safety/status,
  images, and visible error/empty/loading states. It is GET-only and can be
  run against a deployed URL. The interaction smoke refuses non-loopback
  URLs and must run against a disposable DB because it tests validation and
  focus behavior through real clicks (while asserting zero write requests).
  The release E2E self-creates and later destroys a temporary copy of
  `deploy_seed.db`; it is the only browser check allowed to exercise campaign,
  shortlist, ActionItem, quote-override and OutreachLog writes. It cannot be
  pointed at production or the normal local database.
- **Test/UAT audit and narrowly-scoped cleanup**:
  ```bash
  PYTHONPATH=. python3 scripts/audit_and_clean_test_data.py          # dry-run; DB is read-only
  PYTHONPATH=. python3 scripts/audit_and_clean_test_data.py --apply  # exact fingerprints only; auto-backup first
  ```
  Each run regenerates `reports/test_data_audit.json` and `.md` from the
  database actually passed via `--db`. Empty campaign drafts are reported
  as uncertain and retained; only independently identified Runway UAT
  fingerprints are eligible for automatic cleanup. The eight blank
  `SolomonReview` rows are pilot membership configuration, not completed
  human decisions or research evidence, and are always retained.
- **Advisory GPT content/IA review (development only)**:
  ```bash
  PYTHONPATH=. python3 scripts/review_ui_content.py --prepare-only
  PYTHONPATH=. python3 scripts/review_ui_content.py
  ```
  The first command always works offline: it writes a 20-state review packet
  to `reports/ui_content_review/YYYY-MM-DD/input_packet.json`. The second
  reads `OPENAI_API_KEY` and optional `OPENAI_REVIEW_MODEL` from the ignored
  repository `.env`, calls the Responses API with a strict JSON schema, and
  writes `audit.json`, `REPORT.md`, `copy_diff.md`, and
  `information_architecture.md`. It never writes application rows and never
  auto-applies model output to frontend copy. Screenshots and internal UAT
  state leave the local machine in the second mode, so run it only with
  explicit approval for that specific upload.
- **One repeatable refresh command**:
  ```bash
  PYTHONPATH=. python3 scripts/refresh_all.py
  ```
  Runs backup -> migrate -> classify new BD creators -> reconcile, in
  order, stopping at the first fatal failure (classify is non-fatal --
  a missing `OPENAI_API_KEY` shouldn't block reconciliation from running).
- **Recomputing scores after new evidence**: nothing to run. Opportunity
  priority, reachability, creator-campaign-fit, and relationship-strength
  are all computed live from current DB state on every request
  (`bd_compute.py` -- no cache, no stored score column), so newly-confirmed
  operators, new sponsorship evidence, or a fresh X/YouTube enrichment
  pass are reflected on the very next page load.
