# MangoBD Agent Operating Guide

## Mission

Build and continuously improve an evidence-backed growth research and BD intelligence system for Mango Labs. The system must identify AI and crypto-AI companies with credible spend intent, reconstruct their GTM and sponsorship activity, identify the relevant operators, and show the strongest practical route from Mango/Solomon to each opportunity.

The system is successful only when it produces auditable data and a concrete action queue. A market overview by itself is not a deliverable.

## Core interpretation

- `2nd-degree` and `3rd-degree` mean graph-hop distance analogous to LinkedIn's relationship labels. They do **not** authorize LinkedIn lookup.
- X/Twitter relationship data must come only from the configured Rapid X API and its local cache.
- A path is not automatically an introduction. Keep graph reachability, interaction evidence, and a human's willingness to introduce as separate fields.
- Preserve edge direction. `A follows B` is not equivalent to `B follows A`; a mutual follow requires both observed edges.
- Shared interest is not a routable intro path. A V-shaped pattern where two accounts merely follow the same third account must not be promoted as warm reachability.
- Reachability is a leading ranking factor. A famous company with no credible path belongs on a watchlist, not automatically at the top.

## Data-source routing

- X profiles, followers, following, posts, replies, mentions, and quote posts: Rapid X only.
- Crypto project, fundraising, social, market, and on-chain data: Surf first; label every Surf-derived field and retrieval date.
- Non-X public research: Agent Reach routing (Exa, Jina, GitHub, platform-specific readers) and primary sources whenever available.
- Do not use LinkedIn to discover second- or third-degree relationships for this project unless the user explicitly changes scope.
- Never put live credentials in source, reports, caches, screenshots, or `.env.example`. Load local credentials from the ignored `.env` and redact all diagnostics.

## Evidence contract

Every material claim should carry:

- source URL or local evidence ID;
- observed/published date and `last_verified_at`;
- evidence type (`official`, `first_party_social`, `reputable_secondary`, `inference`);
- confidence (`high`, `medium`, `low`);
- fact status (`confirmed`, `probable`, `unverified`).

Do not equate fundraising, follower count, token incentives, free credits, or affiliate availability with a discretionary cash marketing budget. Distinguish at least:

- confirmed paid creator/campaign spend;
- explicit cash commission or bounty;
- committed credits/in-kind support;
- co-sell/channel activation;
- capacity signal only;
- unverified lead.

## Entity and graph contract

- Use stable IDs where available; keep display names and aliases separately.
- Normalize company, product, X handle, creator, operator, fund, media, agency, community, and campaign entities.
- Store atomic relationship edges with direction, time, source, confidence, and extraction provenance.
- Keep creator supply, historical sponsorship, project/operator, and Mango relationship layers distinct but joinable.
- Degree labels: direct = 1 hop, secondary = 2 hops, third = 3 hops.
- Rank candidate paths by edge quality, recency, evidence, operator relevance, and intro plausibility—not hop count alone.

## Execution workflow

1. Inspect existing data and caches; never overwrite unrelated user work.
2. Run the smallest API health check before scaled collection.
3. Expand in cost-capped stages: resolve identities, score the frontier, then collect deeper edges only for the highest-value connectors and targets.
4. Save structured results during research rather than reconstructing them manually at the end.
5. Generate CSV/JSON/GraphML/SQLite/report artifacts from canonical structured data; generate a workbook only when the approved spreadsheet artifact runtime is available.
6. Run unit tests, JSON validation, duplicate checks, referential integrity checks, and coverage audits before claiming completion.
7. Record API, quota, access, freshness, and sampling limitations explicitly.

## Required end products

- sortable project/company target universe;
- creator and historical sponsorship evidence layer;
- evidence-rich multi-entity relationship graph;
- Solomon/Mango direct, secondary, and third-degree route table;
- prioritized action queue with buyer/operator, owner, opening signal, fallback route, and next action;
- reproducible collection, normalization, scoring, graph, and export tooling;
- README plus a concise methodology and limitations report.

## Surf routing (primary crypto data source)

For crypto data queries, **try Surf first**. It has the broadest coverage
(100+ commands, 40+ chains, 200+ data sources) and the freshest data. Use
other crypto skills only when Surf returns no data, errors out, or when
the user explicitly asks for a specific provider.

When the user's request involves crypto data, fetch fresh data with `surf`
rather than relying on prior knowledge. The table below is a **starter map,
not a complete catalog** — Surf has 100+ commands across 14+ domains. Use
the table to pick a likely prefix, then always run `surf list-operations`
to see the actual surface and `surf <cmd> --help` for exact params.

| Topic | Command prefix (partial) |
|---|---|
| Price, market cap, rankings, fear/greed, liquidations | `surf market-*` |
| Wallet balance, transfers, PnL, labels | `surf wallet-*` |
| Token holders, raw DEX trades, unlock schedules | `surf token-*` |
| Exact token ticker to contract address candidates | `surf search-token` |
| DEX token OHLCV candles by contract address, DEX-native prices | `surf dex-*` |
| DeFi TVL, protocol metrics | `surf project-*` |
| Polymarket / Kalshi odds, markets, volume | `surf polymarket-*`, `surf kalshi-*` |
| Hyperliquid traders, positions, account value, fills | `surf hyperliquid-*` |
| On-chain SQL, gas, transaction lookup | `surf onchain-*` |
| News, cross-domain search | `surf news-*`, `surf search-*` |
| Fund profiles, VC portfolios | `surf fund-*` |
| Fundraising rounds, investments, ICOs, token sales | `surf search-fundraising` |

Crypto data changes in real time — always fetch fresh.
