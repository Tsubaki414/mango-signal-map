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
    "Unknown",
]

STRATEGIC_CLASSES = {"Top KOL", "Community Leader", "KOL", "Media / Community Account"}
# "Bulk Distribution Account" is intentionally absent: the product spec's own
# classification enum (§V) only defines KOC / Marketing Account for this
# bucket, so it's never assignable via manual edit or GPT classification --
# including it here would be a permanently unreachable state.
DISTRIBUTION_CLASSES = {"KOC", "Marketing Account"}
# "Unknown" is its own third bucket ("Needs Review" in the UI): creators with
# no classification yet, or too little evidence to classify confidently, are
# NOT defaulted into Strategic KOLs -- they sit in a dedicated review queue
# until a human or a future enrichment pass resolves them.
NEEDS_REVIEW_CLASSES = {"Unknown"}

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

    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=now)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime, default=now, onupdate=now)

    aliases: Mapped[list["CompanyAlias"]] = relationship(back_populates="company", cascade="all, delete-orphan")
    sources: Mapped[list["CompanySource"]] = relationship(back_populates="company", cascade="all, delete-orphan")
    operators: Mapped[list["Operator"]] = relationship(back_populates="company", cascade="all, delete-orphan")
    intro_paths: Mapped[list["IntroPath"]] = relationship(back_populates="company", cascade="all, delete-orphan")
    action_items: Mapped[list["ActionItem"]] = relationship(back_populates="company", cascade="all, delete-orphan")
    sponsorships: Mapped[list["SponsorshipEvidence"]] = relationship(back_populates="company", cascade="all, delete-orphan")
    gtm_case: Mapped["GtmCase | None"] = relationship(back_populates="company", uselist=False)


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

    name: Mapped[str | None] = mapped_column(String(255))
    role: Mapped[str | None] = mapped_column(String(255))
    x_handle: Mapped[str | None] = mapped_column(String(255))
    x_rest_id: Mapped[str | None] = mapped_column(String(60))
    identity_confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    identity_status: Mapped[str | None] = mapped_column(String(255))
    budget_authority_confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    evidence_urls: Mapped[str | None] = mapped_column(Text)  # newline-separated

    company: Mapped[Company] = relationship(back_populates="operators")


class IntroPath(Base):
    """A route from Mango/Solomon to either the company's public account or
    a specific confirmed operator. graph_reachable is a technical-graph
    fact; human_intro_status (willingness to actually introduce) is a
    separate, independently-tracked fact -- never conflate the two."""

    __tablename__ = "intro_paths"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.company_id"))

    target_type: Mapped[str] = mapped_column(String(30))  # company_account | operator_person
    root: Mapped[str] = mapped_column(String(20))  # solomon | mango
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False)
    degree_label: Mapped[str | None] = mapped_column(String(20))  # direct | secondary | third
    hop_count: Mapped[int | None] = mapped_column(Integer)
    connector_handle: Mapped[str | None] = mapped_column(String(255))
    path_labels: Mapped[str | None] = mapped_column(Text)  # " -> "-joined display names along the path
    graph_reachable: Mapped[bool] = mapped_column(Boolean, default=False)
    human_intro_status: Mapped[str] = mapped_column(String(60), default="unvalidated")
    evidence_scope: Mapped[str | None] = mapped_column(String(255))
    source_artifact: Mapped[str | None] = mapped_column(String(255))

    company: Mapped[Company] = relationship(back_populates="intro_paths")


class ActionItem(Base):
    __tablename__ = "action_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.company_id"))

    execution_wave: Mapped[int | None] = mapped_column(Integer)
    action_band: Mapped[str | None] = mapped_column(String(120))
    owner: Mapped[str | None] = mapped_column(String(120))
    primary_next_action: Mapped[str] = mapped_column(Text)
    fallback: Mapped[str | None] = mapped_column(Text)
    success_condition: Mapped[str | None] = mapped_column(Text)
    human_intro_status: Mapped[str | None] = mapped_column(String(60))
    budget_authority_confirmed: Mapped[bool] = mapped_column(Boolean, default=False)

    company: Mapped[Company] = relationship(back_populates="action_items")


class SponsorshipEvidence(Base):
    """One atomic observation of a creator promoting a company: a specific
    video/post, not a rolled-up relationship summary. disclosure_type is
    the strict Kartr-style label -- affiliate and mention must never be
    displayed as if they were paid_sponsorship."""

    __tablename__ = "sponsorship_evidence"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.company_id"))
    creator_id: Mapped[int | None] = mapped_column(ForeignKey("creators.id"))  # null until entity-resolved

    creator_name_raw: Mapped[str] = mapped_column(String(255))
    creator_handle_raw: Mapped[str | None] = mapped_column(String(255))
    platform: Mapped[str] = mapped_column(String(60))
    content_url: Mapped[str] = mapped_column(String(1000))
    content_title: Mapped[str | None] = mapped_column(String(500))
    published_at: Mapped[str | None] = mapped_column(String(60))
    disclosure_type: Mapped[str] = mapped_column(String(30))  # paid_sponsorship | affiliate | mention | unverified
    evidence_text: Mapped[str | None] = mapped_column(Text)
    confidence: Mapped[float | None] = mapped_column(Float)

    company: Mapped[Company] = relationship(back_populates="sponsorships")
    creator: Mapped[Creator | None] = relationship(back_populates="sponsorships")


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
