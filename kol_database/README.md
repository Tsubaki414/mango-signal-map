# Mango Growth & BD Intelligence Cockpit

One app, one database, four modules: **Opportunities** (which AI companies
to chase, ranked by an explainable priority), **Network** (who at Mango/Solomon
can actually get to them), **Creators** (the already-contacted-creator quote
library), and **Campaign Builder** (turn an Opportunity into a budgeted
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

### Current data snapshot (last full run, 2026-08-26, after a full reclassification pass)

301 creators imported. Enrichment coverage:

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
- Three views: **Strategic KOLs**, **KOC / Distribution Inventory**, and
  **Needs Review**. A creator only ever appears in Strategic or
  KOC/Distribution once it has been classified with real evidence --
  unclassified or low-signal creators sit in Needs Review, never defaulted
  into Strategic.
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

Explicitly out of scope for v1 (per product spec): new-KOL discovery,
project/budget-owner research, relationship graphs, outreach CRM,
automated email/Telegram sending, payments/contracts, a data-health
dashboard, fake-follower modeling, a black-box creator score, or
conversion prediction.

## 2. Setup

From the repo root:

```bash
python3 -m venv .venv               # or reuse an existing one
source .venv/bin/activate
pip install -r kol_database/requirements.txt
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
```

`RAPIDAPI_KEY` is also accepted as an alternate name for the Rapid X key.
**No key is ever written into source, logs, cache payloads, or the
frontend.** If a required key is missing, the relevant enrich action is
disabled in the UI and the API returns a 400 telling you what to set,
before making any request.

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

82 tests cover: quote-text parsing (annotated-follower-cell regression,
k/m-multiplier regression, glued-numbered-list regression), platform-name
normalization, container-segment URL handles (`/channel/UC.../`, `/in/`,
`/c/` regression), tab bucketing (Unknown -> Needs Review, not Strategic),
FX conversion, CPM/quote-summary math, Rapid X stat computation
(pinned/repost exclusion, trimmed mean, low-sample flagging), the
Instagram photo-vs-video engagement-rate regression, GPT-result cleaning
(the literal-string-"null" regression), a full import run against the real
`KOL_data/` sheets, and (`test_bd_compute.py`) the Opportunity/reachability/
campaign-fit/relationship-strength scoring logic -- including the
non-negotiable regression that a high-spend, unreachable company must
score Watchlist, never High.

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
script's own docstring for the source-table mapping). It's idempotent --
re-running it deletes and reinserts every BD-owned row, then re-resolves
creators, so running it twice never duplicates data. Last run:

| Entity | Count |
|---|---|
| Companies | 77 |
| Company aliases | 4 |
| Company sources (evidence URLs) | 107 |
| Operators (named people) | 10 confirmed |
| Intro paths | 640 |
| Action items | 42 |
| Sponsorship evidence rows | 52 (36 paid, rest affiliate/mention/event) |
| GTM cases | 8 |
| Connector briefings | 5 (verbatim from `SOLOMON_KOL_COCKPIT.md`) |
| Creators matched to existing quote-library rows | 11 |
| Creators newly created (known via BD research, no Mango quote) | 39 |

Entity resolution matches on `(platform, handle)` case-insensitively. A
sponsor mentioned in the BD evidence who already exists in the Creators
quote library (e.g. `daniel-dann`) is linked to that same row -- one
person, one `creator_id`, visible from both Creators and Opportunities.
A sponsor with no prior quote-library row gets a new `Creator` with
`creator_class="Unknown"` (Needs Review) and zero rate cards, which is the
correct representation of "known from research, never quoted."

### 9.2 The four modules

- **Home** (`/api/home/summary`) -- answers, every load: which company is
  most worth contacting right now (reachability-weighted, not spend-weighted);
  today's highest-value next actions (from `ActionItem`, ordered by
  execution wave); which companies are campaign-ready (High priority *and*
  prior paid-sponsorship evidence); and the most recent paid sponsorship
  evidence found. Doubles as the upgraded Solomon Action Map.
- **Opportunities** (`/api/companies`, `/api/companies/{id}`) -- filterable,
  sortable company list; company detail shows reachability paths, named
  operators, action items, and every sponsorship-evidence row with its
  disclosure type (paid / affiliate / ambassador / event / mention /
  unknown -- never auto-promoted to "paid").
- **Network** (`/api/network/connectors`, `/api/network/operators`) -- the
  5 connector conversations (verbatim from the original briefing) and the
  confirmed-operator roster, each company name a live link into
  Opportunities.
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
- **Company -> Person**: confirmed operators are listed on the company
  page with role and identity-confirmation state.
- **Network -> Company / Opportunity -> Campaign**: connector-brief company
  pills and the "Build a campaign" button both route into the same
  Opportunities/Shortlist objects, not a copy.
- **Global search** (`/api/search`) spans Companies, Operators, and
  Creators from one input in the topbar, and opens the right drawer
  directly.

### 9.4 Scoring (three independent, explainable judgments)

`backend/bd_compute.py` -- every function returns a label plus the plain
`reasons: list[str]` that produced it, never a single opaque number:

- **Opportunity priority** (`opportunity_priority`): reachability is
  weighted highest, deliberately. A company with strong spend evidence but
  no known path to anyone there lands in **Watchlist**, not top priority --
  this is enforced by an explicit rule, not an emergent side effect, and is
  covered by `tests/test_bd_compute.py::TestOpportunityPriority`.
- **Creator campaign fit** (`creator_campaign_fit`): has a Mango quote?
  previously paid-sponsored *this* company (not just mentioned it)?
  category overlap? Each is a separate reason string.
- **Relationship strength** (`relationship_strength`): direct / one-connector
  / multi-hop / cold-contact, derived from `IntroPath.degree_label` and
  `graph_reachable`.

### 9.5 Running the migration

```bash
cd kol_database
PYTHONPATH=. python3 scripts/migrate_bd_data.py
```

Safe to re-run any time the source `mango_bd_v4.sqlite` is refreshed with
new BD research -- it will not duplicate companies or creators.

### 9.6 Known limitations specific to the BD side

- **A handful of sponsorship-evidence "creators" are actually outlets, not
  individual KOLs** (e.g. a tech-news publication credited as an affiliate
  sponsor gets migrated as a `Creator` row like any other sponsor, because
  the source data doesn't distinguish entity type). They surface correctly
  as Needs Review with no quote, so they don't pollute Strategic/Distribution,
  but they will show up in "Suggested creators for this company" alongside
  real individual creators. Filtering these out would need either a
  manual entity-type tag on the source data or a heuristic (e.g. "contains
  a common outlet-name pattern"), neither of which exists yet.
- **26 of the 77 companies (`segment = "existing_v3_ranked"`) have no
  `category`/`geography`** -- that's genuinely missing in the source
  research, not a migration bug; they still get a priority and reachability
  score from what data they do have.
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
