"""SQLAlchemy schema for the Mango KOL database.

A Creator can have several SocialAccounts (one per platform), several
ContactMethods, several RateCards (one per deliverable), and a history of
MetricSnapshots / RecentContent pulled from Rapid X. Shortlists reference
Creators through ShortlistItem so the same creator can sit on several
shortlists with a different chosen deliverable each time.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


def now() -> dt.datetime:
    return dt.datetime.utcnow()


CREATOR_CLASSES = [
    "Top KOL",
    "Community Leader",
    "KOL",
    "KOC",
    "Marketing Account",
    "Media / Community Account",
    "Non-creator / Irrelevant",
    "Unknown",
]

STRATEGIC_CLASSES = {"Top KOL", "Community Leader", "KOL"}
# "Bulk Distribution Account" is intentionally absent: the product spec's own
# classification enum (§V) only defines KOC / Marketing Account for this
# bucket, so it's never assignable via manual edit or GPT classification --
# including it here would be a permanently unreachable state.
DISTRIBUTION_CLASSES = {"KOC", "Marketing Account"}
# Two distinct reasons a creator sits in the "Needs Review" UI tab:
# "Unknown" = not yet classified, a human or a future pass may still resolve
# it into Strategic/Distribution. "Non-creator / Irrelevant" = a *terminal*,
# already-reviewed verdict (e.g. a media outlet, bot, or brand account
# surfaced only because it appeared in BD sponsorship evidence) -- it stays
# visible for audit but must never be offered as a campaign creator
# recommendation (see bd_compute.creator_campaign_fit). Both route to the
# same tab rather than a new one; the creator_class badge text itself is
# what tells the two apart on screen.
NEEDS_REVIEW_CLASSES = {"Unknown", "Non-creator / Irrelevant"}
# Media/community is a distinct, deliberate directory role. It is neither a
# strategic creator recommendation nor an unresolved classification.
MEDIA_CHANNEL_CLASSES = {"Media / Community Account"}
# Classes that must never be suggested as a campaign creator, full stop --
# used by bd_compute/bd_api's Suggested Creators filtering, kept separate
# from NEEDS_REVIEW_CLASSES because "Media / Community Account" is a fully
# legitimate, deliberately-classified creator_class (not a review-queue
# state) that is simply the wrong kind of result for a *creator* recommendation
# list; it still belongs in Strategic and still has its own distribution value
# as a channel, just not mixed into the default suggestion list.
NON_CREATOR_CLASSES = {"Non-creator / Irrelevant"}

PROMOTION_LEVELS = ["Low", "Medium", "High"]
CONFIDENCE_LEVELS = ["Low", "Medium", "High"]


class Creator(Base):
    __tablename__ = "creators"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    display_name: Mapped[str] = mapped_column(String(255))
    primary_handle: Mapped[str | None] = mapped_column(String(255), index=True)

    region: Mapped[str | None] = mapped_column(String(120))
    language: Mapped[str | None] = mapped_column(String(120))
    categories: Mapped[str | None] = mapped_column(String(500))  # comma-separated

    creator_class: Mapped[str] = mapped_column(String(60), default="Unknown")
    creator_class_reason: Mapped[str | None] = mapped_column(Text)
    creator_class_source: Mapped[str] = mapped_column(String(20), default="unset")  # auto | manual | unset
    creator_class_locked: Mapped[bool] = mapped_column(Boolean, default=False)  # manual edits win forever
    classification_confidence: Mapped[str | None] = mapped_column(String(20))  # Low | Medium | High

    promotion_level: Mapped[str | None] = mapped_column(String(20))
    promotion_level_source: Mapped[str] = mapped_column(String(20), default="unset")
    promotion_level_locked: Mapped[bool] = mapped_column(Boolean, default=False)

    internal_notes: Mapped[str | None] = mapped_column(Text)

    source_files: Mapped[str | None] = mapped_column(String(500))  # provenance: which import file(s)

    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=now)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime, default=now, onupdate=now)

    social_accounts: Mapped[list["SocialAccount"]] = relationship(
        back_populates="creator", cascade="all, delete-orphan"
    )
    contacts: Mapped[list["ContactMethod"]] = relationship(
        back_populates="creator", cascade="all, delete-orphan"
    )
    rate_cards: Mapped[list["RateCard"]] = relationship(
        back_populates="creator", cascade="all, delete-orphan"
    )
    campaigns: Mapped[list["CampaignHistory"]] = relationship(
        back_populates="creator", cascade="all, delete-orphan"
    )
    sponsorships: Mapped[list["SponsorshipEvidence"]] = relationship(back_populates="creator")

    @property
    def is_strategic(self) -> bool:
        return self.creator_class in STRATEGIC_CLASSES

    @property
    def is_distribution(self) -> bool:
        return self.creator_class in DISTRIBUTION_CLASSES

    @property
    def needs_review(self) -> bool:
        return self.creator_class in NEEDS_REVIEW_CLASSES


class SocialAccount(Base):
    __tablename__ = "social_accounts"
    __table_args__ = (UniqueConstraint("platform", "handle", name="uq_platform_handle"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    creator_id: Mapped[int] = mapped_column(ForeignKey("creators.id"))

    platform: Mapped[str] = mapped_column(String(60))  # normalized: X, Instagram, YouTube, TikTok, LinkedIn, ...
    platform_raw: Mapped[str | None] = mapped_column(String(300))  # original sheet value, kept for provenance
    handle: Mapped[str | None] = mapped_column(String(255), index=True)
    profile_url: Mapped[str | None] = mapped_column(String(500))

    # Enrichment fields -- populated by Rapid X for platform=="X" or the
    # YouTube Data API v3 for platform=="YouTube"; generic enough to hold
    # either (avatar/bio/followers/avg_views mean the same thing on both).
    x_rest_id: Mapped[str | None] = mapped_column(String(60))
    youtube_channel_id: Mapped[str | None] = mapped_column(String(60))
    avatar_url: Mapped[str | None] = mapped_column(String(500))
    display_name_x: Mapped[str | None] = mapped_column(String(255))
    bio: Mapped[str | None] = mapped_column(Text)
    verified: Mapped[bool | None] = mapped_column(Boolean)
    account_created_at: Mapped[str | None] = mapped_column(String(60))

    followers: Mapped[int | None] = mapped_column(Integer)
    following: Mapped[int | None] = mapped_column(Integer)
    followers_source: Mapped[str] = mapped_column(String(20), default="sheet")  # sheet | rapidx

    avg_views: Mapped[float | None] = mapped_column(Float)
    median_views: Mapped[float | None] = mapped_column(Float)
    engagement_rate: Mapped[float | None] = mapped_column(Float)  # (likes+replies+reposts)/views, trimmed mean basis
    posting_frequency: Mapped[str | None] = mapped_column(String(120))  # e.g. "4.3 posts/day (30d)"
    original_repost_ratio: Mapped[float | None] = mapped_column(Float)  # share of sampled posts that are original
    promotional_content_ratio: Mapped[float | None] = mapped_column(Float)
    content_summary: Mapped[str | None] = mapped_column(Text)  # GPT one-liner: what this account mostly posts about

    last_enriched_at: Mapped[dt.datetime | None] = mapped_column(DateTime)
    enrichment_error: Mapped[str | None] = mapped_column(Text)

    creator: Mapped[Creator] = relationship(back_populates="social_accounts")
    recent_content: Mapped[list["RecentContent"]] = relationship(
        back_populates="social_account", cascade="all, delete-orphan"
    )
    sponsors: Mapped[list["PromotedProject"]] = relationship(
        back_populates="social_account", cascade="all, delete-orphan"
    )


class ContactMethod(Base):
    __tablename__ = "contact_methods"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    creator_id: Mapped[int] = mapped_column(ForeignKey("creators.id"))
    method_type: Mapped[str] = mapped_column(String(30))  # email | telegram | whatsapp | other
    value: Mapped[str] = mapped_column(String(500))

    creator: Mapped[Creator] = relationship(back_populates="contacts")


class RateCard(Base):
    __tablename__ = "rate_cards"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    creator_id: Mapped[int] = mapped_column(ForeignKey("creators.id"))
    platform: Mapped[str | None] = mapped_column(String(60))

    deliverable: Mapped[str] = mapped_column(String(255))
    quote_amount: Mapped[float | None] = mapped_column(Float)  # representative value (midpoint if range)
    quote_amount_min: Mapped[float | None] = mapped_column(Float)
    quote_amount_max: Mapped[float | None] = mapped_column(Float)
    quote_currency: Mapped[str] = mapped_column(String(10), default="USD")
    quote_amount_usd: Mapped[float | None] = mapped_column(Float)
    fx_rate_used: Mapped[float | None] = mapped_column(Float)
    is_package: Mapped[bool] = mapped_column(Boolean, default=False)
    is_confident: Mapped[bool] = mapped_column(Boolean, default=True)  # False = fallback / unparsed row

    raw_quote_text: Mapped[str | None] = mapped_column(Text)  # verbatim source text this line was parsed from
    notes: Mapped[str | None] = mapped_column(Text)

    creator: Mapped[Creator] = relationship(back_populates="rate_cards")


class RecentContent(Base):
    __tablename__ = "recent_content"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    social_account_id: Mapped[int] = mapped_column(ForeignKey("social_accounts.id"))

    post_id: Mapped[str | None] = mapped_column(String(60))
    text: Mapped[str | None] = mapped_column(Text)
    posted_at: Mapped[str | None] = mapped_column(String(60))
    views: Mapped[int | None] = mapped_column(Integer)
    likes: Mapped[int | None] = mapped_column(Integer)
    replies: Mapped[int | None] = mapped_column(Integer)
    reposts: Mapped[int | None] = mapped_column(Integer)
    is_repost: Mapped[bool] = mapped_column(Boolean, default=False)
    is_pinned: Mapped[bool] = mapped_column(Boolean, default=False)

    social_account: Mapped[SocialAccount] = relationship(back_populates="recent_content")


class PromotedProject(Base):
    __tablename__ = "promoted_projects"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    social_account_id: Mapped[int] = mapped_column(ForeignKey("social_accounts.id"))
    project_name: Mapped[str] = mapped_column(String(255))
    mention_count: Mapped[int] = mapped_column(Integer, default=1)
    last_seen_post_id: Mapped[str | None] = mapped_column(String(60))

    social_account: Mapped[SocialAccount] = relationship(back_populates="sponsors")


class CampaignHistory(Base):
    __tablename__ = "campaign_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    creator_id: Mapped[int] = mapped_column(ForeignKey("creators.id"))
    campaign_name: Mapped[str] = mapped_column(String(255))
    campaign_date: Mapped[str | None] = mapped_column(String(60))
    deliverable: Mapped[str | None] = mapped_column(String(255))
    performance_notes: Mapped[str | None] = mapped_column(Text)

    creator: Mapped[Creator] = relationship(back_populates="campaigns")


class Shortlist(Base):
    """A Shortlist *is* a Campaign once it carries campaign metadata --
    there is deliberately no separate Campaign table. Building a campaign
    from an Opportunity just creates a Shortlist with company_id set and
    the objective/audience fields filled in; every existing shortlist
    (budget math, deliverable picking, CSV/XLSX export) keeps working
    unchanged for rows where those new columns are null."""

    __tablename__ = "shortlists"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    client_or_campaign: Mapped[str | None] = mapped_column(String(255))
    budget_usd: Mapped[float | None] = mapped_column(Float)

    company_id: Mapped[str | None] = mapped_column(ForeignKey("companies.company_id"))
    objective: Mapped[str | None] = mapped_column(Text)
    target_audience: Mapped[str | None] = mapped_column(String(500))
    region_pref: Mapped[str | None] = mapped_column(String(120))
    language_pref: Mapped[str | None] = mapped_column(String(120))
    platforms_pref: Mapped[str | None] = mapped_column(String(255))  # comma-separated
    timing: Mapped[str | None] = mapped_column(String(255))
    pricing_assumptions_json: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=now)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime, default=now, onupdate=now)

    items: Mapped[list["ShortlistItem"]] = relationship(
        back_populates="shortlist", cascade="all, delete-orphan"
    )
    company: Mapped["Company | None"] = relationship()


class ShortlistItem(Base):
    __tablename__ = "shortlist_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    shortlist_id: Mapped[int] = mapped_column(ForeignKey("shortlists.id"))
    creator_id: Mapped[int] = mapped_column(ForeignKey("creators.id"))
    rate_card_id: Mapped[int | None] = mapped_column(ForeignKey("rate_cards.id"))

    quote_usd_override: Mapped[float | None] = mapped_column(Float)
    notes: Mapped[str | None] = mapped_column(Text)
    added_at: Mapped[dt.datetime] = mapped_column(DateTime, default=now)

    shortlist: Mapped[Shortlist] = relationship(back_populates="items")
    creator: Mapped[Creator] = relationship()
    rate_card: Mapped[RateCard | None] = relationship()


# =============================================================================
# BD-intelligence entities (Opportunities / Network / Campaign Builder)
#
# Migrated from outputs/pilot_v4/mango_bd_v4.sqlite (the previously-delivered
# Solomon Action Map / AI-company x KOL evidence system) by
# scripts/migrate_bd_data.py -- see that script's docstring for the mapping
# from source tables to these. company_id keeps the source system's stable
# string id ("company:replit") rather than a new surrogate key, so re-running
# the migration is idempotent and every foreign key stays human-readable.
# =============================================================================

SPEND_EVIDENCE_LEVELS = ["L1", "L2", "L3", "legacy_unmapped"]
PRIORITY_TIERS = ["A", "B", "C", "D", "watchlist"]


class Company(Base):
    __tablename__ = "companies"

    company_id: Mapped[str] = mapped_column(String(120), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), unique=True)
    category: Mapped[str | None] = mapped_column(String(255))
    geography: Mapped[str | None] = mapped_column(String(255))
    # Chinese display translations of category/geography. Kept as separate
    # columns rather than translating category/geography in place because
    # bd_compute.creator_campaign_fit() does English word-overlap matching
    # between company.category and Creator.categories (also English) --
    # overwriting the English value would silently break that match.
    category_zh: Mapped[str | None] = mapped_column(String(255))
    geography_zh: Mapped[str | None] = mapped_column(String(255))
    stage: Mapped[str | None] = mapped_column(String(120))

    score_value: Mapped[float | None] = mapped_column(Float)
    score_comparison_group: Mapped[str | None] = mapped_column(String(120))
    priority_tier: Mapped[str | None] = mapped_column(String(20))

    # Budget signal, kept distinct from raw fundraising (a fundraise alone is
    # never treated as a marketing budget -- see AGENTS.md's evidence
    # contract). spend_evidence_level: L1 capacity-only, L2 formal
    # program/in-kind/channel, L3 explicit cash/commission/active spend.
    spend_evidence_level: Mapped[str | None] = mapped_column(String(30))
    spend_mechanism_level: Mapped[str | None] = mapped_column(String(60))
    why_now: Mapped[str | None] = mapped_column(Text)
    budget_evidence: Mapped[str | None] = mapped_column(Text)
    buyer_or_route: Mapped[str | None] = mapped_column(Text)

    website: Mapped[str | None] = mapped_column(String(500))
    x_handle: Mapped[str | None] = mapped_column(String(255))

    internal_notes: Mapped[str | None] = mapped_column(Text)
    raw_json: Mapped[str | None] = mapped_column(Text)  # full source record, for fields not modeled explicitly

    # Set only when a human confirms category/geography/spend evidence is
    # still accurate (or corrects it) -- distinct from updated_at, which
    # also ticks on unattended migration re-runs and doesn't mean a person
    # looked at this row.
    last_verified_at: Mapped[dt.datetime | None] = mapped_column(DateTime)

    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=now)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime, default=now, onupdate=now)

    aliases: Mapped[list["CompanyAlias"]] = relationship(back_populates="company", cascade="all, delete-orphan")
    sources: Mapped[list["CompanySource"]] = relationship(back_populates="company", cascade="all, delete-orphan")
    operators: Mapped[list["Operator"]] = relationship(back_populates="company", cascade="all, delete-orphan")
    intro_paths: Mapped[list["IntroPath"]] = relationship(back_populates="company", cascade="all, delete-orphan")
    action_items: Mapped[list["ActionItem"]] = relationship(back_populates="company", cascade="all, delete-orphan")
    outreach_logs: Mapped[list["OutreachLog"]] = relationship(
        back_populates="company", cascade="all, delete-orphan", order_by="OutreachLog.occurred_at.desc()"
    )
    sponsorships: Mapped[list["SponsorshipEvidence"]] = relationship(back_populates="company", cascade="all, delete-orphan")
    gtm_case: Mapped["GtmCase | None"] = relationship(back_populates="company", uselist=False)
    research_dossier: Mapped["CompanyResearchDossier | None"] = relationship(
        back_populates="company", uselist=False, cascade="all, delete-orphan"
    )
    commercial_evidence: Mapped[list["CompanyCommercialEvidence"]] = relationship(
        back_populates="company", cascade="all, delete-orphan"
    )
    contact_routes: Mapped[list["CompanyContactRoute"]] = relationship(
        back_populates="company", cascade="all, delete-orphan"
    )
    longlist_assessment: Mapped["CompanyLonglistAssessment | None"] = relationship(
        back_populates="company", uselist=False, cascade="all, delete-orphan"
    )
    canonical_decision: Mapped["CompanyDecision | None"] = relationship(
        back_populates="company", uselist=False, cascade="all, delete-orphan"
    )
    asia_profile: Mapped["CompanyAsiaProfile | None"] = relationship(
        back_populates="company", uselist=False, cascade="all, delete-orphan"
    )
    asia_evidence: Mapped[list["CompanyAsiaEvidence"]] = relationship(
        back_populates="company", cascade="all, delete-orphan"
    )
    sales_packet: Mapped["CompanySalesPacket | None"] = relationship(
        back_populates="company", uselist=False, cascade="all, delete-orphan"
    )
    buyer_map_entries: Mapped[list["CompanyBuyerMapEntry"]] = relationship(
        back_populates="company", cascade="all, delete-orphan"
    )
    solomon_review: Mapped["SolomonReview | None"] = relationship(back_populates="company", uselist=False)
    # Lazy on purpose: only the single-company detail view needs this, never
    # the paginated company list, so leaving it un-eager-loaded avoids an
    # N+1 query cost there.
    solomon_review_history: Mapped[list["SolomonReviewHistory"]] = relationship(
        order_by="SolomonReviewHistory.recorded_at.desc()", viewonly=True
    )


class CompanyAlias(Base):
    __tablename__ = "company_aliases"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.company_id"))
    alias: Mapped[str] = mapped_column(String(255))

    company: Mapped[Company] = relationship(back_populates="aliases")


class CompanySource(Base):
    __tablename__ = "company_sources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.company_id"))
    source_url: Mapped[str] = mapped_column(String(1000))

    company: Mapped[Company] = relationship(back_populates="sources")


class Operator(Base):
    """A named person at a target company -- growth/marketing/partnerships/
    community/DevRel, whoever the evidence points to as the actual buyer or
    budget-adjacent contact. Distinct from IntroPath, which is the route
    *to* this person, not the person record itself."""

    __tablename__ = "operators"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.company_id"))
    # The source system's operator_identities.company_id (1 row per company
    # there) -- NULL for operators a human added directly via the UI, which
    # the migration must never touch. Lets migrate_bd_data.py upsert by this
    # key instead of delete-and-reinsert, so a human edit (role, confirmed
    # flags, etc.) survives a source data refresh.
    source_id: Mapped[str | None] = mapped_column(String(120), unique=True, index=True)

    name: Mapped[str | None] = mapped_column(String(255))
    role: Mapped[str | None] = mapped_column(String(255))
    x_handle: Mapped[str | None] = mapped_column(String(255))
    x_rest_id: Mapped[str | None] = mapped_column(String(60))
    identity_confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    identity_status: Mapped[str | None] = mapped_column(String(255))
    budget_authority_confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    evidence_urls: Mapped[str | None] = mapped_column(Text)  # newline-separated
    last_verified_at: Mapped[dt.datetime | None] = mapped_column(DateTime)
    # Explicit human-verification timestamps. `last_verified_at` alone used
    # to be set by any generic edit and was then rendered as if both identity
    # and current role had been checked. Keeping these facts separate prevents
    # an edited handle/title from silently becoming a verification claim.
    identity_human_verified_at: Mapped[dt.datetime | None] = mapped_column(DateTime)
    role_human_verified_at: Mapped[dt.datetime | None] = mapped_column(DateTime)
    budget_authority_verified_at: Mapped[dt.datetime | None] = mapped_column(DateTime)

    # Whether this person's X account currently accepts DMs from non-mutuals
    # (X's own `dm_permissions.can_dm` field on their profile) -- a direct,
    # single-API-call signal that's independent of the whole intro-path graph
    # and its follow-direction ambiguity. NULL = never checked yet.
    can_dm: Mapped[bool | None] = mapped_column(Boolean)
    can_dm_checked_at: Mapped[dt.datetime | None] = mapped_column(DateTime)

    company: Mapped[Company] = relationship(back_populates="operators")
    bridges: Mapped[list["IntroBridge"]] = relationship(back_populates="operator", cascade="all, delete-orphan")


class IntroBridge(Base):
    """A third person who is genuinely mutual-followed by BOTH a Mango-side
    connector AND the target operator -- a credible warm-introduction
    bridge, distinct from IntroPath (which routes through a Mango-side
    connector directly to the target with no guarantee that edge is
    mutual). Found by intersecting the target's following list against a
    Mango-side connector's network and verifying full bidirectionality on
    both sides -- see scripts/find_intro_bridges.py. Only ever created for
    edges that were actually checked in both directions; a company simply
    having none of these rows means no bridge was found, not that none
    exists (search coverage may be partial -- see confidence_note)."""

    __tablename__ = "intro_bridges"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.company_id"))
    operator_id: Mapped[int] = mapped_column(ForeignKey("operators.id"))

    bridge_handle: Mapped[str] = mapped_column(String(255))
    bridge_name: Mapped[str | None] = mapped_column(String(255))
    bridge_bio: Mapped[str | None] = mapped_column(Text)
    bridge_followers_count: Mapped[int | None] = mapped_column(Integer)

    # Which Mango-side account the bridge is mutual with -- usually "Solomon"
    # but kept as a field since other connectors' networks get checked too.
    mango_side_handle: Mapped[str] = mapped_column(String(255))
    mutual_with_mango_side: Mapped[bool] = mapped_column(Boolean, default=False)
    mutual_with_target: Mapped[bool] = mapped_column(Boolean, default=False)

    confidence_note: Mapped[str | None] = mapped_column(Text)
    discovered_at: Mapped[dt.datetime] = mapped_column(DateTime, default=now)

    # Deprecated flat summary fields from the first (2026-08-28, same-day)
    # interaction check -- superseded by InteractionCheck/InteractionEvidence
    # below, which keep a fully auditable per-tweet trail instead of a
    # collapsed count + one sample. Left populated for that first batch
    # rather than deleted (never delete data), but no longer written to;
    # read interaction_checks instead.
    interaction_checked_at: Mapped[dt.datetime | None] = mapped_column(DateTime)
    interaction_count_recent: Mapped[int | None] = mapped_column(Integer)
    most_recent_interaction_at: Mapped[dt.datetime | None] = mapped_column(DateTime)
    interaction_sample_text: Mapped[str | None] = mapped_column(Text)

    company: Mapped[Company] = relationship()
    operator: Mapped[Operator] = relationship(back_populates="bridges")
    interaction_checks: Mapped[list["InteractionCheck"]] = relationship(
        back_populates="bridge", cascade="all, delete-orphan"
    )


class InteractionCheck(Base):
    """Full audit trail for one *directional* Rapid X interaction query --
    which two accounts, which direction, the exact query string, when it
    ran, and the API's known coverage limits. A→B and B→A are always
    logged as separate rows (never one OR'd query merged into one), per
    the 2026-08-28 spec's explicit requirement to avoid direction
    ambiguity about which side actually produced a match. "No rows in
    `evidence`" must render as "not found within this query's coverage",
    never as "these two don't know each other" -- a negative result is
    bounded by what the API actually searched, not a proof of absence."""

    __tablename__ = "interaction_checks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    bridge_id: Mapped[int] = mapped_column(ForeignKey("intro_bridges.id"))

    from_handle: Mapped[str] = mapped_column(String(255))
    to_handle: Mapped[str] = mapped_column(String(255))
    query_string: Mapped[str] = mapped_column(Text)
    checked_at: Mapped[dt.datetime] = mapped_column(DateTime, default=now)
    coverage_note: Mapped[str] = mapped_column(
        Text,
        default="基于 X 搜索接口单次查询结果，接口对历史推文的索引范围不透明、不保证完整覆盖；未命中仅代表本次查询范围内未发现，不代表两人不认识或历史上从无互动。",
    )

    bridge: Mapped[IntroBridge] = relationship(back_populates="interaction_checks")
    evidence: Mapped[list["InteractionEvidence"]] = relationship(back_populates="check", cascade="all, delete-orphan")


class InteractionEvidence(Base):
    """One concrete tweet found by an InteractionCheck -- kept as an
    independently re-verifiable, clickable record (tweet id + URL), not a
    truncated text blob standing in for "proof". If a tweet's id/URL
    couldn't be recovered from the API response, is_auditable is False
    and that must be shown plainly rather than presenting the leftover
    text as complete evidence."""

    __tablename__ = "interaction_evidence"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    check_id: Mapped[int] = mapped_column(ForeignKey("interaction_checks.id"))

    tweet_id: Mapped[str | None] = mapped_column(String(60))
    tweet_url: Mapped[str | None] = mapped_column(String(500))
    posted_at: Mapped[dt.datetime | None] = mapped_column(DateTime)
    author_handle: Mapped[str | None] = mapped_column(String(255))
    recipient_handle: Mapped[str | None] = mapped_column(String(255))
    context_text: Mapped[str | None] = mapped_column(Text)
    interaction_type: Mapped[str | None] = mapped_column(String(20))  # reply | mention | quote | event
    is_auditable: Mapped[bool] = mapped_column(Boolean, default=True)

    check: Mapped[InteractionCheck] = relationship(back_populates="evidence")


class IntroPath(Base):
    """A route from Mango/Solomon to either the company's public account or
    a specific confirmed operator. graph_reachable is a technical-graph
    fact; human_intro_status (willingness to actually introduce) is a
    separate, independently-tracked fact -- never conflate the two."""

    __tablename__ = "intro_paths"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.company_id"))
    # x_paths.path_id or operator_person_paths.operator_path_id -- NULL for
    # a path a human added directly. See Operator.source_id for why this
    # exists: upsert-by-key instead of delete-and-reinsert on refresh.
    source_id: Mapped[str | None] = mapped_column(String(120), unique=True, index=True)

    target_type: Mapped[str] = mapped_column(String(30))  # company_account | operator_person
    root: Mapped[str] = mapped_column(String(20))  # solomon | mango
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False)
    degree_label: Mapped[str | None] = mapped_column(String(20))  # direct | secondary | third
    hop_count: Mapped[int | None] = mapped_column(Integer)
    connector_handle: Mapped[str | None] = mapped_column(String(255))
    path_labels: Mapped[str | None] = mapped_column(Text)  # " -> "-joined display names along the path
    # Exact JSON array copied from the source graph.  Do not derive this
    # from ``path_labels``: node order is a route traversal, not proof that
    # the left node follows the right node.  Older source batches use
    # symbolic per-hop values (follows/followed_by/mutual_follow); Mango-root
    # batches may instead contain explicit atomic edges ("@a -> @b").
    edge_directions: Mapped[str | None] = mapped_column(Text)
    direction_data_unavailable: Mapped[bool] = mapped_column(Boolean, default=False)
    direction_data_unavailable_reason: Mapped[str | None] = mapped_column(Text)
    graph_reachable: Mapped[bool] = mapped_column(Boolean, default=False)
    human_intro_status: Mapped[str] = mapped_column(String(60), default="unvalidated")
    evidence_scope: Mapped[str | None] = mapped_column(String(255))
    source_artifact: Mapped[str | None] = mapped_column(String(255))

    company: Mapped[Company] = relationship(back_populates="intro_paths")


class ActionItem(Base):
    __tablename__ = "action_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.company_id"))
    # action_queue.action_id -- NULL for an action item a human added
    # directly. See Operator.source_id for why this exists.
    source_id: Mapped[str | None] = mapped_column(String(120), unique=True, index=True)

    execution_wave: Mapped[int | None] = mapped_column(Integer)
    action_band: Mapped[str | None] = mapped_column(String(120))
    owner: Mapped[str | None] = mapped_column(String(120))
    primary_next_action: Mapped[str] = mapped_column(Text)
    fallback: Mapped[str | None] = mapped_column(Text)
    success_condition: Mapped[str | None] = mapped_column(Text)
    human_intro_status: Mapped[str | None] = mapped_column(String(60))
    budget_authority_confirmed: Mapped[bool] = mapped_column(Boolean, default=False)

    # Execution tracking, editable from the UI -- ActionItem otherwise only
    # carries the BD-research recommendation, not whether Mango/Solomon
    # actually did anything about it yet.
    status: Mapped[str] = mapped_column(String(20), default="open")  # open | in_progress | done | blocked
    due_date: Mapped[str | None] = mapped_column(String(20))  # "YYYY-MM-DD", free-typed, no timezone math needed
    outcome_notes: Mapped[str | None] = mapped_column(Text)

    # Lets the frontend flag "new since your last visit" on the home page --
    # existing rows are backfilled to the migration date (see
    # scripts/add_created_at_columns.py), so nothing already on file falsely
    # shows as new; only genuinely new rows going forward carry a real date.
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=now)

    company: Mapped[Company] = relationship(back_populates="action_items")


ACTION_ITEM_STATUSES = ["open", "in_progress", "done", "blocked"]


class OutreachLog(Base):
    """A single real-world event in the outreach funnel for one company --
    "we messaged the connector," "the connector confirmed they know the
    target," "no response after 2 weeks," etc. Every reachability/priority
    score in this system up to 2026-08-28 is a research hypothesis, not a
    measured outcome: nothing here records whether a bridge candidate
    actually replied, whether a DM got a response, or whether a proposal
    ever led to a meeting. Without that, the scoring has no way to learn
    which signal (a bridge, an open DM, a proposal-backed cold DM)
    actually converts -- see the 2026-08-28 read-only audit's explicit
    call for this. Human-entered only; nothing here is inferred or
    fabricated by any script. One company can have many rows, one per
    event, forming a timeline -- not a single "status" field that gets
    silently overwritten and loses history."""

    __tablename__ = "outreach_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.company_id"))
    # The target operator this event is about -- kept as `operator_id` for
    # schema continuity, always means "target", never the Mango-side owner.
    operator_id: Mapped[int | None] = mapped_column(ForeignKey("operators.id"))
    # The connector/bridge candidate this event is about, if the contact
    # attempt went through a third party rather than direct-to-target.
    bridge_id: Mapped[int | None] = mapped_column(ForeignKey("intro_bridges.id"))
    intro_path_id: Mapped[int | None] = mapped_column(ForeignKey("intro_paths.id"))
    linked_action_item_id: Mapped[int | None] = mapped_column(ForeignKey("action_items.id"))

    owner: Mapped[str | None] = mapped_column(String(120))  # who at Mango did this
    contacted_who: Mapped[str | None] = mapped_column(String(255))  # free text: name/handle actually reached
    contact_channel: Mapped[str | None] = mapped_column(String(40))  # dm | email | form | connector_ask | in_person | other
    evidence_url: Mapped[str | None] = mapped_column(String(500))  # screenshot/thread/doc link, optional

    stage: Mapped[str] = mapped_column(String(40))
    notes: Mapped[str | None] = mapped_column(Text)
    occurred_at: Mapped[dt.datetime] = mapped_column(DateTime, default=now)
    next_follow_up_date: Mapped[str | None] = mapped_column(String(20))  # "YYYY-MM-DD", free-typed

    # Corrections happen (wrong company, test entry, logged against the
    # wrong connector) -- voiding keeps the row and the audit trail intact
    # instead of a hard delete losing history. A voided row is excluded
    # from relationship-stage auto-progression and from funnel learning,
    # but stays visible with its reason.
    voided: Mapped[bool] = mapped_column(Boolean, default=False)
    voided_reason: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=now)

    company: Mapped[Company] = relationship(back_populates="outreach_logs")
    operator: Mapped["Operator | None"] = relationship()
    bridge: Mapped["IntroBridge | None"] = relationship()
    intro_path: Mapped["IntroPath | None"] = relationship()
    linked_action_item: Mapped["ActionItem | None"] = relationship()


# The funnel stages the 2026-08-28 audit asked for by name -- deliberately
# a flat list of discrete events, not a linear pipeline: "no_response" and
# "rejected" are terminal outcomes reachable from several earlier stages,
# not just the last step of a happy path.
OUTREACH_STAGES = [
    "contact_attempted",
    "connector_replied",
    "relationship_confirmed",
    "relationship_rejected",
    "intro_accepted",
    "intro_made",
    "target_replied",
    "meeting_booked",
    "proposal_requested",
    "rejected",
    "no_response",
]

CONTACT_CHANNELS = ["dm", "email", "form", "connector_ask", "in_person", "other"]


class SponsorshipEvidence(Base):
    """One atomic observation of a creator promoting a company: a specific
    video/post, not a rolled-up relationship summary. disclosure_type is
    the strict Kartr-style label -- affiliate and mention must never be
    displayed as if they were paid_sponsorship."""

    __tablename__ = "sponsorship_evidence"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.company_id"))
    creator_id: Mapped[int | None] = mapped_column(ForeignKey("creators.id"))  # null until entity-resolved
    # sponsor_observations.observation_id -- see Operator.source_id for why
    # this exists (upsert-by-key on refresh instead of delete-and-reinsert,
    # so a Confirm/Reject review survives a source data refresh).
    source_id: Mapped[str | None] = mapped_column(String(120), unique=True, index=True)

    creator_name_raw: Mapped[str] = mapped_column(String(255))
    creator_handle_raw: Mapped[str | None] = mapped_column(String(255))
    platform: Mapped[str] = mapped_column(String(60))
    content_url: Mapped[str] = mapped_column(String(1000))
    content_title: Mapped[str | None] = mapped_column(String(500))
    published_at: Mapped[str | None] = mapped_column(String(60))
    # Canonical API vocabulary is defined in SPONSORSHIP_EVIDENCE_TYPES.
    # Legacy `mention` / `unverified` values remain valid source values and
    # are mapped at serialization time so old research is preserved without
    # being confused with paid evidence.
    disclosure_type: Mapped[str] = mapped_column(String(30))
    evidence_text: Mapped[str | None] = mapped_column(Text)
    confidence: Mapped[float | None] = mapped_column(Float)

    # A human checking the underlying content_url against what the row
    # claims -- separate from `confidence`, which is the migration's own
    # extraction confidence, not a person's verification.
    review_status: Mapped[str] = mapped_column(String(20), default="unreviewed")  # unreviewed | confirmed | rejected
    reviewed_at: Mapped[dt.datetime | None] = mapped_column(DateTime)
    reviewed_note: Mapped[str | None] = mapped_column(Text)

    # See ActionItem.created_at -- same "new since last visit" purpose,
    # backfilled the same way for pre-existing rows.
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=now)

    company: Mapped[Company] = relationship(back_populates="sponsorships")
    creator: Mapped[Creator | None] = relationship(back_populates="sponsorships")


SPONSORSHIP_REVIEW_STATUSES = ["unreviewed", "confirmed", "rejected"]
SPONSORSHIP_EVIDENCE_TYPES = [
    "paid_sponsorship",
    "affiliate",
    "ambassador_long_term_partner",
    "event_podcast_appearance",
    "organic_mention",
    "unknown_commercial_relationship",
]


class GtmCase(Base):
    __tablename__ = "gtm_cases"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    company_id: Mapped[str | None] = mapped_column(ForeignKey("companies.company_id"))
    company_name: Mapped[str] = mapped_column(String(255))
    category: Mapped[str | None] = mapped_column(String(255))
    maturity_stage: Mapped[str | None] = mapped_column(String(120))
    gtm_motion: Mapped[str | None] = mapped_column(Text)
    spend_classification: Mapped[str | None] = mapped_column(String(120))
    what_mango_should_copy: Mapped[str | None] = mapped_column(Text)
    what_not_to_copy: Mapped[str | None] = mapped_column(Text)

    company: Mapped[Company | None] = relationship(back_populates="gtm_case")


class CompanyLonglistAssessment(Base):
    """Company-first commercial triage, independent of the relationship graph.

    ``commercial_priority_score`` intentionally excludes X reachability and
    connector availability.  Those remain contact-route facts, not discovery
    or target-value inputs.
    """

    __tablename__ = "company_longlist_assessments"

    company_id: Mapped[str] = mapped_column(ForeignKey("companies.company_id"), primary_key=True)
    disposition: Mapped[str] = mapped_column(String(40))
    icp_type: Mapped[str] = mapped_column(String(80))
    commercial_priority_score: Mapped[float | None] = mapped_column(Float)
    discovery_paths_json: Mapped[str] = mapped_column(Text, default="[]")
    keep_reason: Mapped[str | None] = mapped_column(Text)
    downgrade_reason: Mapped[str | None] = mapped_column(Text)
    confidence: Mapped[str] = mapped_column(String(20), default="medium")
    fact_status: Mapped[str] = mapped_column(String(30), default="mixed")
    last_verified_at: Mapped[dt.datetime | None] = mapped_column(DateTime)

    company: Mapped[Company] = relationship(back_populates="longlist_assessment")


class CompanyResearchDossier(Base):
    """One Solomon-ready company dossier for the commercial priority set."""

    __tablename__ = "company_research_dossiers"

    company_id: Mapped[str] = mapped_column(ForeignKey("companies.company_id"), primary_key=True)
    priority_rank: Mapped[int] = mapped_column(Integer, index=True)
    commercial_priority_score: Mapped[float] = mapped_column(Float)
    icp_type: Mapped[str] = mapped_column(String(80))
    business_model: Mapped[str | None] = mapped_column(Text)
    target_customer: Mapped[str | None] = mapped_column(Text)
    why_company: Mapped[str] = mapped_column(Text)
    why_now: Mapped[str] = mapped_column(Text)
    commercialization_evidence: Mapped[str] = mapped_column(Text)
    budget_spend_signals: Mapped[str] = mapped_column(Text)
    gtm_summary: Mapped[str] = mapped_column(Text)
    marketing_channels_json: Mapped[str] = mapped_column(Text, default="[]")
    historical_campaigns_json: Mapped[str] = mapped_column(Text, default="[]")
    creators_media_communities_json: Mapped[str] = mapped_column(Text, default="[]")
    repeated_activity: Mapped[bool] = mapped_column(Boolean, default=False)
    operator_id: Mapped[int | None] = mapped_column(ForeignKey("operators.id"))
    operator_role_relevance: Mapped[str | None] = mapped_column(Text)
    public_contact_method: Mapped[str | None] = mapped_column(Text)
    mango_service_fit: Mapped[str] = mapped_column(Text)
    mango_offer: Mapped[str] = mapped_column(Text)
    opening_angle: Mapped[str] = mapped_column(Text)
    confidence: Mapped[str] = mapped_column(String(20), default="medium")
    fact_status: Mapped[str] = mapped_column(String(30), default="mixed")
    key_unknowns_json: Mapped[str] = mapped_column(Text, default="[]")
    last_verified_at: Mapped[dt.datetime] = mapped_column(DateTime)

    company: Mapped[Company] = relationship(back_populates="research_dossier")
    operator: Mapped[Operator | None] = relationship()


class CompanyDecision(Base):
    """Canonical, product-facing commercial and execution conclusion.

    The underlying numeric research score remains available for sorting and
    audit, but this row is the one conclusion Home, Opportunities and Company
    detail must all display.  Reachability can influence execution readiness;
    it never changes opportunity value.
    """

    __tablename__ = "company_decisions"

    company_id: Mapped[str] = mapped_column(ForeignKey("companies.company_id"), primary_key=True)
    opportunity_value: Mapped[str] = mapped_column(String(20))
    execution_readiness: Mapped[str] = mapped_column(String(40))
    decision_bucket: Mapped[str] = mapped_column(String(30))
    opportunity_reason_zh: Mapped[str] = mapped_column(Text)
    why_now_zh: Mapped[str] = mapped_column(Text)
    what_to_sell_zh: Mapped[str] = mapped_column(Text)
    buyer_summary_zh: Mapped[str] = mapped_column(Text)
    primary_route_summary_zh: Mapped[str] = mapped_column(Text)
    first_action_zh: Mapped[str] = mapped_column(Text)
    fallback_1_zh: Mapped[str] = mapped_column(Text)
    fallback_2_zh: Mapped[str] = mapped_column(Text)
    key_unknowns_json: Mapped[str] = mapped_column(Text, default="[]")
    last_verified_at: Mapped[dt.datetime] = mapped_column(DateTime)

    company: Mapped[Company] = relationship(back_populates="canonical_decision")


class CompanyCommercialEvidence(Base):
    """Atomic non-X commercial/GTM evidence supporting a dossier claim."""

    __tablename__ = "company_commercial_evidence"
    __table_args__ = (UniqueConstraint("evidence_id", name="uq_company_commercial_evidence_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    evidence_id: Mapped[str] = mapped_column(String(180), index=True)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.company_id"), index=True)
    claim: Mapped[str] = mapped_column(Text)
    source_url: Mapped[str] = mapped_column(String(1000))
    evidence_date: Mapped[str | None] = mapped_column(String(60))
    date_basis: Mapped[str | None] = mapped_column(String(40))
    evidence_type: Mapped[str] = mapped_column(String(60))
    confidence: Mapped[str] = mapped_column(String(20))
    fact_status: Mapped[str] = mapped_column(String(30))
    last_verified_at: Mapped[dt.datetime] = mapped_column(DateTime)

    company: Mapped[Company] = relationship(back_populates="commercial_evidence")


class CompanyContactRoute(Base):
    """One independently described route to a target company or operator."""

    __tablename__ = "company_contact_routes"
    __table_args__ = (
        UniqueConstraint("company_id", "route_key", name="uq_company_contact_route_key"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.company_id"), index=True)
    route_key: Mapped[str] = mapped_column(String(120))
    route_type: Mapped[str] = mapped_column(String(30))  # Direct | Intermediary | Warm candidate | Cold
    label: Mapped[str] = mapped_column(String(255))
    route_detail: Mapped[str] = mapped_column(Text)
    why_this_route: Mapped[str] = mapped_column(Text)
    required_first_step: Mapped[str] = mapped_column(Text)
    supporting_evidence: Mapped[str | None] = mapped_column(Text)
    evidence_url: Mapped[str | None] = mapped_column(String(1000))
    confidence: Mapped[str] = mapped_column(String(20))
    is_best: Mapped[bool] = mapped_column(Boolean, default=False)
    fallback_order: Mapped[int | None] = mapped_column(Integer)
    last_verified_at: Mapped[dt.datetime] = mapped_column(DateTime)

    company: Mapped[Company] = relationship(back_populates="contact_routes")


class CompanyAsiaProfile(Base):
    """One reviewed Asia-market conclusion per company.

    This is intentionally separate from geography and founder background.
    The profile records a market/GTM conclusion; its child evidence rows hold
    the atomic first-party facts supporting that conclusion.  A company may
    be Asia-headquartered and global-first without being described as an
    overseas company "expanding into Asia".
    """

    __tablename__ = "company_asia_profiles"

    company_id: Mapped[str] = mapped_column(ForeignKey("companies.company_id"), primary_key=True)
    asia_interest_status: Mapped[str] = mapped_column(String(40))
    market_context: Mapped[str] = mapped_column(String(60))
    asia_target_markets_json: Mapped[str] = mapped_column(Text, default="[]")
    asia_signal_types_json: Mapped[str] = mapped_column(Text, default="[]")
    asia_signal_summary: Mapped[str] = mapped_column(Text)
    confidence: Mapped[str] = mapped_column(String(20))
    asia_operator: Mapped[str | None] = mapped_column(String(255))
    localization_status: Mapped[str | None] = mapped_column(Text)
    local_partner_or_customer: Mapped[str | None] = mapped_column(Text)
    regional_creator_activity: Mapped[str | None] = mapped_column(Text)
    mango_asia_fit: Mapped[str | None] = mapped_column(Text)
    recommended_market_entry_angle: Mapped[str | None] = mapped_column(Text)
    review_scope: Mapped[str] = mapped_column(Text)
    last_verified_at: Mapped[dt.datetime] = mapped_column(DateTime)

    company: Mapped[Company] = relationship(back_populates="asia_profile")


class CompanyAsiaEvidence(Base):
    """Atomic first-party Asia market, GTM, hiring or partnership evidence."""

    __tablename__ = "company_asia_evidence"
    __table_args__ = (UniqueConstraint("evidence_id", name="uq_company_asia_evidence_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    evidence_id: Mapped[str] = mapped_column(String(180), index=True)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.company_id"), index=True)
    target_markets_json: Mapped[str] = mapped_column(Text, default="[]")
    signal_type: Mapped[str] = mapped_column(String(60))
    summary: Mapped[str] = mapped_column(Text)
    source_url: Mapped[str] = mapped_column(String(1000))
    evidence_date: Mapped[str | None] = mapped_column(String(60))
    collected_at: Mapped[dt.datetime] = mapped_column(DateTime)
    confidence: Mapped[str] = mapped_column(String(20))
    fact_status: Mapped[str] = mapped_column(String(30))
    last_verified_at: Mapped[dt.datetime] = mapped_column(DateTime)

    company: Mapped[Company] = relationship(back_populates="asia_evidence")


class CompanySalesPacket(Base):
    """Send-ready commercial brief for the highest-priority companies.

    Price totals are intentionally not stored here. They are calculated at
    request time from current, confident creator rate cards; optional Mango
    fees only appear when an internal user supplies the assumptions.
    """

    __tablename__ = "company_sales_packets"

    company_id: Mapped[str] = mapped_column(ForeignKey("companies.company_id"), primary_key=True)
    packet_version: Mapped[str] = mapped_column(String(30), default="v7")
    offer_name_zh: Mapped[str] = mapped_column(String(255))
    commercial_thesis_zh: Mapped[str] = mapped_column(Text)
    audience_zh: Mapped[str] = mapped_column(Text)
    deliverables_json: Mapped[str] = mapped_column(Text, default="[]")
    measurement_json: Mapped[str] = mapped_column(Text, default="[]")
    qualification_questions_json: Mapped[str] = mapped_column(Text, default="[]")
    switch_rules_json: Mapped[str] = mapped_column(Text, default="[]")
    email_subject_en: Mapped[str] = mapped_column(String(500))
    email_body_en: Mapped[str] = mapped_column(Text)
    x_dm_en: Mapped[str] = mapped_column(Text)
    evidence_note_zh: Mapped[str] = mapped_column(Text)
    confidence: Mapped[str] = mapped_column(String(20))
    last_verified_at: Mapped[dt.datetime] = mapped_column(DateTime)

    company: Mapped[Company] = relationship(back_populates="sales_packet")


class CompanyBuyerMapEntry(Base):
    """A buyer, execution-owner or procurement route with its own truth state."""

    __tablename__ = "company_buyer_map_entries"
    __table_args__ = (
        UniqueConstraint("company_id", "entry_key", name="uq_company_buyer_map_entry_key"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.company_id"), index=True)
    entry_key: Mapped[str] = mapped_column(String(120))
    buyer_type: Mapped[str] = mapped_column(String(40))
    name: Mapped[str | None] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(255))
    relevance_zh: Mapped[str] = mapped_column(Text)
    verification_status: Mapped[str] = mapped_column(String(50))
    contact_method: Mapped[str | None] = mapped_column(String(500))
    route_type: Mapped[str] = mapped_column(String(30))
    source_url: Mapped[str | None] = mapped_column(String(1000))
    evidence_date: Mapped[str | None] = mapped_column(String(60))
    confidence: Mapped[str] = mapped_column(String(20))
    next_step_zh: Mapped[str] = mapped_column(Text)
    fallback_order: Mapped[int | None] = mapped_column(Integer)
    last_verified_at: Mapped[dt.datetime] = mapped_column(DateTime)

    company: Mapped[Company] = relationship(back_populates="buyer_map_entries")


class ConnectorBrief(Base):
    """The hand-curated "compress N targets into 5 connector conversations"
    briefing (previously delivered as SOLOMON_KOL_COCKPIT.md section 2).
    This is synthesized judgment, not mechanically re-derivable from the
    graph -- migrated verbatim rather than regenerated, per instruction not
    to reconstruct prior human/AI analysis from scratch."""

    __tablename__ = "connector_briefs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    connector_name: Mapped[str] = mapped_column(String(255))
    connector_x_handle: Mapped[str | None] = mapped_column(String(255))
    company_ids: Mapped[str] = mapped_column(Text)  # comma-separated Company.company_id list
    best_current_action: Mapped[str] = mapped_column(Text)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)


# =============================================================================
# Data-integrity sprint additions: creator-recommendation exclusions and the
# Solomon UAT review flow. Both are thin, additive tables -- neither touches
# the stable Creators/Opportunities/Network/Campaign-Builder core.
# =============================================================================


class CreatorExclusion(Base):
    """A human explicitly saying "don't suggest this creator here again."
    company_id=None means the exclusion is global/category-wide (e.g. the
    creator turned out to be the wrong niche entirely); a set company_id
    scopes it to just that one company's suggestions."""

    __tablename__ = "creator_exclusions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    creator_id: Mapped[int] = mapped_column(ForeignKey("creators.id"))
    company_id: Mapped[str | None] = mapped_column(ForeignKey("companies.company_id"))
    reason: Mapped[str] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=now)

    creator: Mapped[Creator] = relationship()
    company: Mapped["Company | None"] = relationship()


class CreatorEntityAlias(Base):
    """Redirect a retired duplicate creator id to its canonical entity.

    The row preserves old links after a high-confidence cross-platform merge.
    Merges are intentionally narrower than fuzzy name matching: the current
    rule requires the exact normalized public name and the same public
    business email.
    """

    __tablename__ = "creator_entity_aliases"

    alias_creator_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    canonical_creator_id: Mapped[int] = mapped_column(ForeignKey("creators.id"), index=True)
    alias_display_name: Mapped[str] = mapped_column(String(255))
    merge_basis: Mapped[str] = mapped_column(Text)
    merged_at: Mapped[dt.datetime] = mapped_column(DateTime, default=now)

    canonical_creator: Mapped[Creator] = relationship()


SOLOMON_DECISIONS = ["proceed", "watch", "reject"]


class SolomonReview(Base):
    """One row per company in the Solomon UAT pilot set -- selecting a
    company into the pilot *is* creating this row, there's no separate
    "is_pilot" flag. Captures the human decision at the end of a full
    Opportunity -> evidence -> operator -> intro path -> campaign ->
    suggested creators -> budget -> next action walkthrough, so it's a
    decision record, not a data field computed by the app."""

    __tablename__ = "solomon_reviews"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.company_id"), unique=True)

    decision: Mapped[str | None] = mapped_column(String(20))  # proceed | watch | reject
    intro_path_confirmed_real: Mapped[bool | None] = mapped_column(Boolean)
    creator_suggestions_sellable: Mapped[bool | None] = mapped_column(Boolean)
    missing_info: Mapped[str | None] = mapped_column(Text)
    solomon_notes: Mapped[str | None] = mapped_column(Text)
    next_action: Mapped[str | None] = mapped_column(Text)
    reviewed_at: Mapped[dt.datetime | None] = mapped_column(DateTime)

    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=now)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime, default=now, onupdate=now)

    company: Mapped[Company] = relationship(back_populates="solomon_review")


class SolomonReviewHistory(Base):
    """Append-only snapshot written every time a SolomonReview is saved.
    SolomonReview itself only ever holds the current state (each save
    overwrites decision/notes/next_action in place) -- without this table
    there's no way to see what was decided last time, or why it changed."""

    __tablename__ = "solomon_review_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.company_id"), index=True)

    decision: Mapped[str | None] = mapped_column(String(20))
    intro_path_confirmed_real: Mapped[bool | None] = mapped_column(Boolean)
    creator_suggestions_sellable: Mapped[bool | None] = mapped_column(Boolean)
    missing_info: Mapped[str | None] = mapped_column(Text)
    solomon_notes: Mapped[str | None] = mapped_column(Text)
    next_action: Mapped[str | None] = mapped_column(Text)

    recorded_at: Mapped[dt.datetime] = mapped_column(DateTime, default=now)

    company: Mapped[Company] = relationship()
