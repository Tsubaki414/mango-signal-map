"""Mango Signal Map schema.

Signal Map is its own product with its own database. The previous-generation
Mango BD system (``kol_database/``) is a *parts source*: its hand-collected
data is migrated in once by ``signal_map/scripts/migrate_from_bd.py``, after
which ``kol.db`` is a read-only archive and this schema is the only truth.

Three structural rules this file exists to enforce:

1. **Four paths are never merged.** ``商务触达路径`` (how Mango reaches a
   target), ``商业采购路径`` (who Mango buys through), ``KOL–Root 注意力路径``
   (why a creator reaches a target circle), and ``投放与转化路径`` (what
   audiences actually did) are different questions with different evidence.
   Only the second and third have tables here; path 1 belongs to the internal
   operating surface and path 4 does not exist until campaigns run. Nothing
   in this file may be read as evidence for a path it does not name.

2. **Client visibility is opt-in.** Every field a client could see is either
   on an explicit allowlist in ``client_safe.py`` or gated by a per-row
   ``client_visible`` flag defaulting to False. Adding a column must never
   silently expose it.

3. **Unknown is a value.** Missing data is stored as NULL and rendered as
   未知/待确认. No layer of this system may fill a gap with a guess.
"""

from __future__ import annotations

import datetime as dt
import secrets

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


class Base(DeclarativeBase):
    pass


# =============================================================================
# Controlled vocabularies
#
# Plain tuples rather than SQL enums: SQLite has no native enum, and these
# lists grow as Mango learns. Validation happens at the service layer so a
# migration never fails on a value the DB has held for months.
# =============================================================================

#: Where a price came from. Drives how much the number can be trusted and
#: whether it may ever reach a client.
QUOTE_SOURCES = (
    "kol_direct",        # the creator quoted Mango directly
    "manager_agency",    # a manager / agency / MCN quoted on their behalf
    "supplier",          # a supplier's roster price -- floor price, internal only
    "public_rate_card",  # published media kit
    "historical_deal",   # what a past campaign actually paid
    "internal_estimate", # Mango's own estimate -- never a client-facing price
    "unknown",
)

#: Nature of the quote, per the product spec. Survives all the way into the
#: client layer -- a client must always know how solid a price is.
QUOTE_STATUSES = (
    "confirmed_valid",       # 已确认且仍有效
    "confirmed_may_expire",  # 已确认但可能过期
    "historical",            # 历史成交
    "supplier_provided",     # 供应商提供
    "public",                # 公开报价
    "internal_estimate",     # 内部估算
    "needs_review",          # 需要复核
    "no_quote",              # 对方明确未报价 / 要求先给项目细节
)

#: Client-safe statuses. Anything else must be summarised, never shown raw.
CLIENT_VISIBLE_QUOTE_STATUSES = ("confirmed_valid", "confirmed_may_expire", "public")

#: Normalised deliverable formats. ``other`` is legitimate; ``unknown`` means
#: the parser could not tell and a human should look.
CONTENT_FORMATS = (
    "x_single_post", "x_thread", "x_quote_repost", "x_repost", "x_space",
    "youtube_dedicated", "youtube_integration", "youtube_short",
    "instagram_post", "instagram_reel", "instagram_story",
    "tiktok_video", "linkedin_post", "threads_post", "telegram_post",
    "newsletter", "article", "podcast", "livestream", "voiceover",
    "package", "other", "unknown",
)

PLATFORMS = (
    "X", "YouTube", "Instagram", "TikTok", "LinkedIn", "Threads",
    "Telegram", "Facebook", "Newsletter", "Podcast", "Multi", "Unknown",
)

#: How much to trust the machine parse of a free-text quote.
PARSE_CONFIDENCE = ("high", "medium", "low", "unparsed")

# --- creator taxonomy, carried from Mango BD ---------------------------------
#
# BD's KOL / 泛KOC split is a real commercial distinction and is preserved
# verbatim: a 战略型 KOL is bought for voice and credibility, a 分发型 KOC or
# marketing account is bought for volume and cost. A combination that mixes
# them deliberately is a strategy; one that mixes them by accident is a bad
# recommendation, so the tier has to be filterable rather than buried in a
# free-text class name.

CREATOR_CLASSES = (
    "Top KOL", "Community Leader", "KOL",
    "KOC", "Marketing Account",
    "Media / Community Account",
    "Non-creator / Irrelevant",
    "Unknown",
)

#: strategic  = 战略型 KOL: original voice, credibility, real discussion
#: distribution = 泛 KOC / 铺量: reach and cost efficiency, not authority
#: media      = 媒体/社区账号: a channel, not a personal voice
#: non_creator = terminal reviewed verdict -- never recommendable
CREATOR_TIERS = ("strategic", "distribution", "media", "non_creator", "unknown")

CREATOR_TIER_LABELS_ZH = {
    "strategic": "战略型 KOL", "distribution": "泛 KOC / 铺量",
    "media": "媒体 / 社区账号", "non_creator": "非创作者", "unknown": "待分类",
}

_CLASS_TO_TIER = {
    "Top KOL": "strategic", "Community Leader": "strategic", "KOL": "strategic",
    "KOC": "distribution", "Marketing Account": "distribution",
    "Media / Community Account": "media",
    "Non-creator / Irrelevant": "non_creator",
    "Unknown": "unknown",
}

#: Never recommendable, full stop. A news outlet, bot, or brand entity that
#: only appeared because it was mentioned somewhere has no creator
#: relationship with an audience to sell.
NON_RECOMMENDABLE_TIERS = frozenset({"non_creator"})

PROMOTION_LEVELS = ("Low", "Medium", "High")


def tier_for_class(creator_class: str | None) -> str:
    """Map a BD creator class to its commercial tier. Unknown classes fall to
    ``unknown`` rather than to a tier they were never assigned."""
    return _CLASS_TO_TIER.get(creator_class or "", "unknown")

#: Client decision on a recommended object.
#: 客户能做的动作。
#:
#: ``interest`` 是为**还没有报价的新发现对象**加的，和 ``approve`` 不是一回事：
#: approve 意味着「这个人我要了」，前提是价格和档期都能确认；interest 意味着
#: 「这个人我感兴趣，你去谈」。把两者合并会逼客户在没有价格的情况下做承诺，
#: 而这恰恰是新发现对象的常态。它的去向也不同 —— interest 直接变成 BD 的
#: 建联优先级，这是「客户的选择驱动资源库扩张」那条闭环的起点。
FEEDBACK_ACTIONS = (
    "approve", "reject", "question", "interest", "shortlist", "unshortlist",
)

#: Internal follow-up kinds a client action can generate.
TASK_KINDS = (
    "price_inquiry",       # 报价待确认 -> 询价
    "price_review",        # 报价需复核
    "contact_gap",         # 联系方式缺失 -> 商务路径补充
    "client_question",     # Question 转内部行动项
    "availability_check",  # 档期确认
    "evidence_gap",        # 受众/地区/证据补充
    # 待建联队列产生的两种工作。和 ``price_inquiry`` 分开，是因为它们的完成
    # 标准不同:确认意愿的产出是一句「接/不接」,询价的产出是一个数字,而在没
    # 确认意愿之前就去询价,拿到的往往是沉默而不是拒绝。
    "client_interest",     # 客户表达兴趣 -> 优先建联
    "willingness_check",   # 确认是否承接第三方投放
    "sample_collection",   # 补充近期样例与受众证据
    "other",
)

TASK_STATUSES = ("open", "in_progress", "done", "blocked", "cancelled")

#: Fact-status vocabulary from docs/product-language.md. Binding on any
#: generated copy.
FACT_STATUSES = ("verified_fact", "high_confidence_inference", "research_lead", "unknown", "stale")


# =============================================================================
# Supply layer -- migrated from the BD system, owned by Signal Map from now on
# =============================================================================


class Creator(Base):
    """A person or account Mango can buy from.

    Split from the BD system's ``creators`` table with the free-text columns
    normalised: ``language`` held "English"/"en"/NULL interchangeably and
    ``region`` was 85% empty, so both are replaced by explicit normalised
    columns plus a preserved raw value. The raw columns exist so a bad
    normalisation can always be re-derived rather than being a lossy import.
    """

    __tablename__ = "creators"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    display_name: Mapped[str] = mapped_column(String(255))
    primary_handle: Mapped[str | None] = mapped_column(String(255), index=True)

    # --- normalised, queryable ---------------------------------------------
    #: ISO-639-1 codes, comma-separated ("en", "en,zh"). NULL = 未知.
    languages: Mapped[str | None] = mapped_column(String(120), index=True)
    #: ISO-3166-1 alpha-2 of where the *creator* is based ("US"). NULL = 未知.
    #: This is NOT audience geography -- see ``audience_markets``.
    base_country: Mapped[str | None] = mapped_column(String(2), index=True)
    #: Coarse market bucket -- the field clients actually filter on. Derived,
    #: never observed: see ``normalize.derive_market_region``. A precise
    #: audience-geography filter would hide 84% of sellable inventory, so this
    #: is deliberately 笼统 (English-language ⇒ 欧美) and always carries its
    #: basis so a card can say 依据：内容语言 instead of implying measurement.
    market_region: Mapped[str | None] = mapped_column(String(30), index=True)
    #: language | country | provenance | bio_script | unknown
    market_region_basis: Mapped[str] = mapped_column(String(20), default="unknown")

    #: Measured audience geography. Distinct from ``market_region`` and still
    #: NULL everywhere: the BD system never collected it, and a creator's own
    #: location is not evidence about where their audience is. Filled only by
    #: real research, never by inference.
    audience_markets: Mapped[str | None] = mapped_column(String(255))
    #: Normalised verticals, comma-separated (see normalize.VERTICALS).
    verticals: Mapped[str | None] = mapped_column(String(255), index=True)
    #: Who this creator's content addresses (founders / developers / traders
    #: / ...). Derived like ``market_region``: inferred from the creator's own
    #: bio and content summary, falling back to their verticals. This is not
    #: measured audience demographics -- nobody has measured those.
    audience_types: Mapped[str | None] = mapped_column(String(255), index=True)
    #: content | vertical | unknown
    audience_types_basis: Mapped[str] = mapped_column(String(20), default="unknown")
    #: The phrase behind each assigned type, e.g. ``founders:"passive income"``.
    #: Some inputs (recent posts) are transient, so without this the judgment
    #: would be unauditable.
    audience_types_evidence: Mapped[str | None] = mapped_column(Text)

    # --- preserved raw values ----------------------------------------------
    language_raw: Mapped[str | None] = mapped_column(String(120))
    region_raw: Mapped[str | None] = mapped_column(String(120))
    categories_raw: Mapped[str | None] = mapped_column(String(500))

    creator_class: Mapped[str] = mapped_column(String(60), default="Unknown", index=True)
    creator_class_confidence: Mapped[str | None] = mapped_column(String(20))
    #: KOL vs 泛KOC, derived from ``creator_class`` -- see ``tier_for_class``.
    #: Filterable because "买声量还是买铺量" is a decision the client makes.
    creator_tier: Mapped[str] = mapped_column(String(20), default="unknown", index=True)
    #: How promotional their feed reads (Low/Medium/High). Brand-safety input.
    promotion_level: Mapped[str | None] = mapped_column(String(20))

    #: Internal-only. Never serialised to a client under any circumstance.
    internal_notes: Mapped[str | None] = mapped_column(Text)

    # --- provenance ---------------------------------------------------------
    source_system: Mapped[str | None] = mapped_column(String(60))
    source_ref: Mapped[str | None] = mapped_column(String(255))
    #: When Mango first recorded this creator. A lower bound on data age, not
    #: a quote date and not a verification date.
    first_seen_at: Mapped[dt.datetime | None] = mapped_column(DateTime)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=now)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime, default=now, onupdate=now)

    accounts: Mapped[list["SocialAccount"]] = relationship(
        back_populates="creator", cascade="all, delete-orphan"
    )
    quotes: Mapped[list["Quote"]] = relationship(
        back_populates="creator", cascade="all, delete-orphan"
    )
    contacts: Mapped[list["ContactMethod"]] = relationship(
        back_populates="creator", cascade="all, delete-orphan"
    )
    sponsorships: Mapped[list["SponsorshipEvidence"]] = relationship(
        back_populates="creator", cascade="all, delete-orphan"
    )
    procurement_routes: Mapped[list["ProcurementRoute"]] = relationship(
        back_populates="creator", cascade="all, delete-orphan"
    )

    @property
    def has_priced_quote(self) -> bool:
        """MVP candidate-pool gate: at least one quote carrying a real number."""
        return any(q.amount_usd is not None for q in self.quotes)

    @property
    def is_recommendable(self) -> bool:
        """False for entities that have no audience relationship to sell.

        A price alone does not make something recommendable -- BD's
        ``Non-creator / Irrelevant`` verdict is terminal and must gate the
        candidate pool, not merely sort lower.
        """
        return self.creator_tier not in NON_RECOMMENDABLE_TIERS


class SocialAccount(Base):
    __tablename__ = "social_accounts"
    __table_args__ = (UniqueConstraint("platform", "handle", name="uq_sm_platform_handle"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    creator_id: Mapped[int] = mapped_column(ForeignKey("creators.id"), index=True)

    platform: Mapped[str] = mapped_column(String(60), index=True)
    handle: Mapped[str | None] = mapped_column(String(255), index=True)
    profile_url: Mapped[str | None] = mapped_column(String(500))
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False)

    #: Platform-native stable id (X rest_id, YouTube channel id). Handles get
    #: renamed; this does not. Required for any graph work: follow endpoints
    #: return numeric ids, so without this a roster account cannot be matched
    #: against a follow list at all -- which silently produced false "no
    #: relationship" results until it was noticed.
    platform_uid: Mapped[str | None] = mapped_column(String(60), index=True)

    avatar_url: Mapped[str | None] = mapped_column(String(500))
    bio: Mapped[str | None] = mapped_column(Text)
    verified: Mapped[bool | None] = mapped_column(Boolean)

    followers: Mapped[int | None] = mapped_column(Integer)
    avg_views: Mapped[float | None] = mapped_column(Float)
    median_views: Mapped[float | None] = mapped_column(Float)
    engagement_rate: Mapped[float | None] = mapped_column(Float)
    posting_frequency: Mapped[str | None] = mapped_column(String(120))
    promotional_content_ratio: Mapped[float | None] = mapped_column(Float)
    content_summary: Mapped[str | None] = mapped_column(Text)

    #: Age of the metrics above. A client card must show this, not imply live data.
    metrics_observed_at: Mapped[dt.datetime | None] = mapped_column(DateTime)

    creator: Mapped[Creator] = relationship(back_populates="accounts")


class ContactMethod(Base):
    """INTERNAL ONLY. No client serializer may reference this table."""

    __tablename__ = "contact_methods"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    creator_id: Mapped[int] = mapped_column(ForeignKey("creators.id"), index=True)
    method_type: Mapped[str] = mapped_column(String(30))
    value: Mapped[str] = mapped_column(String(500))
    #: Whether a human confirmed this actually reaches the person.
    verified_at: Mapped[dt.datetime | None] = mapped_column(DateTime)
    verified_by: Mapped[str | None] = mapped_column(String(120))

    #: Where this came from, and when we saw it. The BD import carried 392
    #: contacts with neither, so every one of them renders as 未核验·来源未记录
    #: -- which is the honest reading, and the reason these columns exist:
    #: a contact with no provenance cannot be re-checked when it bounces.
    source_url: Mapped[str | None] = mapped_column(String(500))
    source_note: Mapped[str | None] = mapped_column(String(255))
    observed_at: Mapped[dt.date | None] = mapped_column(Date)
    #: True only if the address was constructed rather than read. Guessing an
    #: email is forbidden, so this exists to make a violation visible rather
    #: than to license one.
    is_inferred: Mapped[bool] = mapped_column(Boolean, default=False)

    creator: Mapped[Creator] = relationship(back_populates="contacts")


# =============================================================================
# Quote layer -- the core asset
# =============================================================================


class QuoteMessage(Base):
    """One verbatim quote communication, before any parsing.

    Every ``Quote`` points back here. Re-parsing is therefore always possible
    and never destructive: fix the parser, re-run, and the source text is
    untouched. 699 rows in the BD system collapsed to 254 distinct texts,
    which is the real unit -- one message usually prices several deliverables.
    """

    __tablename__ = "quote_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    creator_id: Mapped[int] = mapped_column(ForeignKey("creators.id"), index=True)

    #: Verbatim. Never edited, never normalised, never truncated.
    raw_text: Mapped[str] = mapped_column(Text)

    source: Mapped[str] = mapped_column(String(40), default="unknown")  # QUOTE_SOURCES
    #: True when ``source`` was inferred from provenance rather than recorded.
    source_is_inferred: Mapped[bool] = mapped_column(Boolean, default=False)
    #: Who provided it, when known ("Creators Agency", "@handle"). Internal only.
    source_detail: Mapped[str | None] = mapped_column(String(255))

    #: When the quote was actually given. NULL = 未知 -- the BD import has no
    #: such data, and it must not be back-filled from the import date.
    quoted_at: Mapped[dt.date | None] = mapped_column(Date)
    #: Latest date Mango knows the text existed by. Weaker than ``quoted_at``
    #: but enough to say "报价至少早于 X" instead of nothing.
    observed_at: Mapped[dt.date | None] = mapped_column(Date)
    valid_until: Mapped[dt.date | None] = mapped_column(Date)

    source_system: Mapped[str | None] = mapped_column(String(60))
    source_ref: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=now)

    creator: Mapped[Creator] = relationship()
    quotes: Mapped[list["Quote"]] = relationship(
        back_populates="message", cascade="all, delete-orphan"
    )


class Quote(Base):
    """One priced deliverable, parsed out of a ``QuoteMessage``.

    Replaces the BD system's ``RateCard``, which carried only amount /
    currency / platform / raw text. Everything the product spec requires --
    source, date, validity, confirmation status, quantity, package structure,
    internal cost, client price, display permission -- lives here.

    Cost/price separation is the point of this table:
    ``internal_cost_usd`` (what Mango pays) and ``client_price_usd`` (what the
    client is quoted) are different columns, and only the latter can ever be
    serialised outward. There is deliberately no ``margin`` column: margin is
    derived internally, never stored where a serializer could reach it.
    """

    __tablename__ = "quotes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    creator_id: Mapped[int] = mapped_column(ForeignKey("creators.id"), index=True)
    message_id: Mapped[int] = mapped_column(ForeignKey("quote_messages.id"), index=True)

    # --- what is being bought ----------------------------------------------
    #: Verbatim label as written by the quoter, e.g. "YT专属视频".
    deliverable_raw: Mapped[str | None] = mapped_column(String(300))
    content_format: Mapped[str] = mapped_column(String(40), default="unknown", index=True)
    platform: Mapped[str] = mapped_column(String(40), default="Unknown", index=True)
    quantity: Mapped[int] = mapped_column(Integer, default=1)

    is_package: Mapped[bool] = mapped_column(Boolean, default=False)
    #: Human-readable contents of a package. Packages cannot be summed with
    #: single-item prices, so budget maths must branch on ``is_package``.
    package_contents: Mapped[str | None] = mapped_column(Text)

    # --- the number ---------------------------------------------------------
    amount: Mapped[float | None] = mapped_column(Float)
    amount_min: Mapped[float | None] = mapped_column(Float)
    amount_max: Mapped[float | None] = mapped_column(Float)
    currency: Mapped[str] = mapped_column(String(10), default="USD")
    amount_usd: Mapped[float | None] = mapped_column(Float, index=True)
    fx_rate_used: Mapped[float | None] = mapped_column(Float)
    #: Reference FX table version, so an old conversion stays explainable.
    fx_asof: Mapped[str | None] = mapped_column(String(20))

    # --- how much to trust it ----------------------------------------------
    status: Mapped[str] = mapped_column(String(30), default="needs_review", index=True)
    needs_review: Mapped[bool] = mapped_column(Boolean, default=True)
    review_reason: Mapped[str | None] = mapped_column(Text)
    parse_confidence: Mapped[str] = mapped_column(String(20), default="low")
    parse_notes: Mapped[str | None] = mapped_column(Text)
    #: The exact slice of ``QuoteMessage.raw_text`` this row was parsed from.
    raw_segment: Mapped[str | None] = mapped_column(Text)

    # --- commercial ---------------------------------------------------------
    #: INTERNAL ONLY -- what Mango pays. Must never reach a client response.
    internal_cost_usd: Mapped[float | None] = mapped_column(Float)
    #: Exact price cleared for the client. NULL means "价格待 Mango 确认".
    #: Deliberately NOT set equal to ``internal_cost_usd``: that number is what
    #: the creator asked Mango for, so showing it would expose Mango's cost and
    #: leave no margin.
    client_price_usd: Mapped[float | None] = mapped_column(Float)
    #: Coarse band ("$1,000–2,500") derived from cost. Client-safe: it places
    #: the creator in a budget tier without revealing the cost or the markup,
    #: which is what the spec allows when only an internal cost exists
    #: (预算可覆盖 / 参考区间).
    client_price_band: Mapped[str | None] = mapped_column(String(40))
    #: Hard gate. False by default: an imported quote is never client-visible
    #: until a human clears it.
    client_visible: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    client_visibility_note: Mapped[str | None] = mapped_column(Text)

    minimum_spend_usd: Mapped[float | None] = mapped_column(Float)
    usage_rights: Mapped[str | None] = mapped_column(Text)
    exclusivity: Mapped[str | None] = mapped_column(Text)
    turnaround: Mapped[str | None] = mapped_column(String(120))

    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=now)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime, default=now, onupdate=now)

    creator: Mapped[Creator] = relationship(back_populates="quotes")
    message: Mapped[QuoteMessage] = relationship(back_populates="quotes")

    @property
    def is_priced(self) -> bool:
        return self.amount_usd is not None

    @property
    def client_display_price(self) -> float | None:
        """The only exact price a client serializer may read.

        Returns None -- rendered as 价格待 Mango 确认 -- whenever the quote is
        not cleared for display. Never falls back to ``internal_cost_usd``,
        which is Mango's cost.
        """
        if not self.client_visible:
            return None
        return self.client_price_usd

    @property
    def client_display_band(self) -> str | None:
        """Coarse budget band, shown when no exact client price is set yet."""
        if not self.client_visible:
            return None
        return self.client_price_band


# =============================================================================
# Evidence layer
# =============================================================================


class SponsorshipEvidence(Base):
    """历史合作 / 赞助证据. Commercial signal, explicitly NOT a relationship."""

    __tablename__ = "sponsorship_evidence"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    creator_id: Mapped[int] = mapped_column(ForeignKey("creators.id"), index=True)

    sponsor_name: Mapped[str | None] = mapped_column(String(255))
    evidence_type: Mapped[str | None] = mapped_column(String(60))  # paid_sponsorship | affiliate | mention
    review_status: Mapped[str] = mapped_column(String(30), default="unreviewed")
    platform: Mapped[str | None] = mapped_column(String(60))
    post_url: Mapped[str | None] = mapped_column(String(500))
    published_at: Mapped[dt.date | None] = mapped_column(Date)
    notes: Mapped[str | None] = mapped_column(Text)

    #: Only ``paid_sponsorship`` + ``confirmed`` may be called 已复核付费赞助证据.
    #: Anything else renders as 付费观察·待审核.
    client_visible: Mapped[bool] = mapped_column(Boolean, default=False)

    source_system: Mapped[str | None] = mapped_column(String(60))
    source_ref: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=now)

    creator: Mapped[Creator] = relationship(back_populates="sponsorships")


class ProcurementRoute(Base):
    """商业采购路径 (path 2): if the client buys this creator, who does Mango
    buy through?

    Deliberately separate from any 商务触达 record. "Fiona can reach Creators
    Agency through a colleague" is path 1; "Creators Agency represents this
    creator and can quote for them" is this table. The same organisation
    appearing in both does not license merging them.

    A client only ever sees the fact that Mango handles procurement -- never
    the supplier's identity, floor price, or commission.
    """

    __tablename__ = "procurement_routes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    creator_id: Mapped[int] = mapped_column(ForeignKey("creators.id"), index=True)

    #: direct | manager | agency | mcn | supplier | media_sales
    route_type: Mapped[str] = mapped_column(String(40), default="direct")
    counterparty_name: Mapped[str | None] = mapped_column(String(255))
    is_exclusive: Mapped[bool | None] = mapped_column(Boolean)
    provides_quotes: Mapped[bool] = mapped_column(Boolean, default=False)
    commission_note: Mapped[str | None] = mapped_column(Text)
    accepts_commercial_work: Mapped[bool | None] = mapped_column(Boolean)

    #: Whether a human verified this route works. Unverified is the default;
    #: an unverified route is 研究线索, never a usable path.
    verified_at: Mapped[dt.datetime | None] = mapped_column(DateTime)
    verified_by: Mapped[str | None] = mapped_column(String(120))

    #: How firmly the representation is established. Default is the weakest
    #: reading on purpose: **finding an agency's page does not establish that
    #: it represents this creator.** Public rosters list people an agency once
    #: worked with, aspires to work with, or simply features. Only ``confirmed``
    #: may be read as a usable procurement route.
    representation_status: Mapped[str] = mapped_column(
        String(30), default="public_entry_unverified"
    )
    #: The page the claim was read from, so it can be re-checked.
    source_url: Mapped[str | None] = mapped_column(String(500))
    #: How to reach the counterparty. Internal only, like every contact.
    contact_value: Mapped[str | None] = mapped_column(String(500))

    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=now)

    creator: Mapped[Creator] = relationship(back_populates="procurement_routes")


class AttentionSignal(Base):
    """KOL–Root 注意力路径 (path 3). **Not populated in the MVP.**

    The table exists so the recommendation engine can add this axis later
    without a rewrite, and so prior research has a typed home. It is empty
    today and the client layer must render 关系数据待补充 rather than
    substituting any path-1 data.

    A follow proves a follow signal existed at observation time. It does not
    prove endorsement, exposure, timing, or conversion. Zero rows means
    当前未观察到, never 不存在关系.
    """

    __tablename__ = "attention_signals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    #: Free-form node refs so a Root that is not yet a Creator can be stored.
    source_node: Mapped[str] = mapped_column(String(255), index=True)
    target_node: Mapped[str] = mapped_column(String(255), index=True)
    source_creator_id: Mapped[int | None] = mapped_column(ForeignKey("creators.id"))

    platform: Mapped[str | None] = mapped_column(String(60))
    #: follow | reply | quote | mention | repost | co_appearance | co_mention
    signal_type: Mapped[str] = mapped_column(String(40))
    #: source_to_target | target_to_source | bidirectional
    direction: Mapped[str] = mapped_column(String(30))
    occurred_at: Mapped[dt.date | None] = mapped_column(Date)
    observation_count: Mapped[int] = mapped_column(Integer, default=1)

    raw_evidence: Mapped[str | None] = mapped_column(Text)
    evidence_url: Mapped[str | None] = mapped_column(String(500))
    collected_at: Mapped[dt.datetime | None] = mapped_column(DateTime)
    #: Explicit statement of what the collection could NOT see.
    coverage_limitation: Mapped[str | None] = mapped_column(Text)
    confidence: Mapped[str] = mapped_column(String(20), default="research_lead")
    human_confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    #: Employment, ownership, or paid relationship that disqualifies the
    #: signal as independent attention.
    conflict_of_interest: Mapped[str | None] = mapped_column(Text)

    source_system: Mapped[str | None] = mapped_column(String(60))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=now)


# =============================================================================
# Client decision layer
# =============================================================================


class Client(Base):
    """The buying organisation. Distinct from a creator's past sponsor."""

    __tablename__ = "clients"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    website: Mapped[str | None] = mapped_column(String(500))
    industry: Mapped[str | None] = mapped_column(String(255))
    internal_notes: Mapped[str | None] = mapped_column(Text)

    #: Bearer token identifying this client. Every client request is scoped to
    #: the client it resolves to, so one client can never read another's
    #: briefs, candidates or feedback.
    api_token: Mapped[str | None] = mapped_column(String(64), unique=True, index=True)

    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=now)

    briefs: Mapped[list["Brief"]] = relationship(back_populates="client", cascade="all, delete-orphan")


class Brief(Base):
    """项目简报 + 客户偏好.

    The BD system's ``Shortlist`` had already merged brief, candidate list and
    budget into one row and that worked, but it could not express language,
    audience type, objective, risk tolerance, or must-include/exclude sets.
    Here the brief is its own object and the candidate list hangs off it, so
    one brief can carry several competing 方案 later.
    """

    __tablename__ = "briefs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    client_id: Mapped[int] = mapped_column(ForeignKey("clients.id"), index=True)
    name: Mapped[str] = mapped_column(String(255))

    #: 发给客户的 Brief 码。**不可用主键代替**：自增 id 意味着 /api/brief/1、
    #: /api/brief/2 能被顺着数出来，而这个接口会返回项目名、方向、市场与预算档。
    #:
    #: 默认值必须在模型上，不能只靠一次性回填脚本 —— 否则之后新建的每一条
    #: Brief 都没有码，``/api/brief`` 永远找不到它，而且是安静地 404。
    access_code: Mapped[str] = mapped_column(
        String(32), unique=True, index=True, default=lambda: secrets.token_urlsafe(9)
    )

    # --- project understanding ---------------------------------------------
    product_description: Mapped[str | None] = mapped_column(Text)
    verticals: Mapped[str | None] = mapped_column(String(255))
    stage: Mapped[str | None] = mapped_column(String(60))
    competitors: Mapped[str | None] = mapped_column(Text)

    # --- targeting ----------------------------------------------------------
    target_markets: Mapped[str | None] = mapped_column(String(255))   # ISO-2 CSV
    content_languages: Mapped[str | None] = mapped_column(String(120))  # ISO-639-1 CSV
    target_audiences: Mapped[str | None] = mapped_column(String(255))
    #: 客户选定的目标人物（root）handle，逗号分隔。这是 target-backed discovery
    #: 的入口：候选池由这些人的关注与互动网络算出来，换一组人名单就换一份。
    target_root_handles: Mapped[str | None] = mapped_column(Text)
    #: ai / crypto / finance / tech。决定客户看到哪一套 Root 库。
    domain_group: Mapped[str | None] = mapped_column(String(30))
    objectives: Mapped[str | None] = mapped_column(String(255))
    platforms: Mapped[str | None] = mapped_column(String(255))
    content_formats: Mapped[str | None] = mapped_column(String(255))

    # --- budget -------------------------------------------------------------
    total_budget_usd: Mapped[float | None] = mapped_column(Float)
    per_creator_budget_min_usd: Mapped[float | None] = mapped_column(Float)
    per_creator_budget_max_usd: Mapped[float | None] = mapped_column(Float)

    # --- constraints --------------------------------------------------------
    creator_size_preference: Mapped[str | None] = mapped_column(String(120))
    object_types: Mapped[str | None] = mapped_column(String(255))
    risk_tolerance: Mapped[str | None] = mapped_column(String(40))
    must_include_creator_ids: Mapped[str | None] = mapped_column(String(500))
    must_exclude_creator_ids: Mapped[str | None] = mapped_column(String(500))
    notes: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=now)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime, default=now, onupdate=now)

    client: Mapped[Client] = relationship(back_populates="briefs")
    runs: Mapped[list["RecommendationRun"]] = relationship(
        back_populates="brief", cascade="all, delete-orphan"
    )


class RecommendationRun(Base):
    """One 推荐批次. Immutable once written -- a re-run creates a new row.

    Feedback references the run it reacted to, so "the client rejected this"
    always resolves to the exact ruleset and candidate set they saw.
    """

    __tablename__ = "recommendation_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    brief_id: Mapped[int] = mapped_column(ForeignKey("briefs.id"), index=True)

    rule_version: Mapped[str] = mapped_column(String(40))
    #: Serialised brief snapshot: a later brief edit must not silently rewrite
    #: the meaning of an earlier run.
    brief_snapshot_json: Mapped[str | None] = mapped_column(Text)
    candidate_pool_size: Mapped[int | None] = mapped_column(Integer)
    returned_count: Mapped[int | None] = mapped_column(Integer)
    #: Filters that could not be applied because the data does not exist.
    unsupported_filters: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=now)

    brief: Mapped[Brief] = relationship(back_populates="runs")
    items: Mapped[list["Recommendation"]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )


class Recommendation(Base):
    """One recommended object inside a run, with its reasoning preserved.

    ``axis_scores_json`` holds the per-dimension judgments; there is a
    ``rank_score`` for ordering but it is explicitly not the product -- no
    client surface may show it alone, and every row must carry the axis
    breakdown, matched preferences, unmet conditions and missing data that
    produced it.
    """

    __tablename__ = "recommendations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("recommendation_runs.id"), index=True)
    creator_id: Mapped[int] = mapped_column(ForeignKey("creators.id"), index=True)
    quote_id: Mapped[int | None] = mapped_column(ForeignKey("quotes.id"))

    rank: Mapped[int | None] = mapped_column(Integer)
    #: Internal ordering value. Never rendered on its own.
    rank_score: Mapped[float | None] = mapped_column(Float)

    axis_scores_json: Mapped[str | None] = mapped_column(Text)
    matched_preferences_json: Mapped[str | None] = mapped_column(Text)
    unmet_conditions_json: Mapped[str | None] = mapped_column(Text)
    missing_data_json: Mapped[str | None] = mapped_column(Text)
    #: Rule-generated, template-filled. Never model-authored free text.
    reasons_json: Mapped[str | None] = mapped_column(Text)
    risk_flags_json: Mapped[str | None] = mapped_column(Text)
    suggested_roles_json: Mapped[str | None] = mapped_column(Text)
    next_step: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=now)

    run: Mapped[RecommendationRun] = relationship(back_populates="items")
    creator: Mapped[Creator] = relationship()
    quote: Mapped[Quote | None] = relationship()


class CandidateItem(Base):
    """客户候选名单. Scoped to a brief -- never a global judgment on a creator."""

    __tablename__ = "candidate_items"
    __table_args__ = (UniqueConstraint("brief_id", "creator_id", name="uq_brief_creator"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    brief_id: Mapped[int] = mapped_column(ForeignKey("briefs.id"), index=True)
    creator_id: Mapped[int] = mapped_column(ForeignKey("creators.id"), index=True)
    quote_id: Mapped[int | None] = mapped_column(ForeignKey("quotes.id"))

    #: Chosen cooperation form, when the client picked among several quotes.
    selected_content_format: Mapped[str | None] = mapped_column(String(40))
    quantity: Mapped[int] = mapped_column(Integer, default=1)
    client_note: Mapped[str | None] = mapped_column(Text)

    added_at: Mapped[dt.datetime] = mapped_column(DateTime, default=now)
    removed_at: Mapped[dt.datetime | None] = mapped_column(DateTime)

    brief: Mapped[Brief] = relationship()
    creator: Mapped[Creator] = relationship()
    quote: Mapped[Quote | None] = relationship()


class FeedbackEvent(Base):
    """Approve / Reject / Question, keyed by 客户 + 项目 + 对象.

    Append-only: a later approve does not delete an earlier reject, because
    the sequence is itself the signal that re-ranks the next run.

    A reject removes the creator from *this brief only*. Nothing in this table
    may downgrade a creator in the supply layer.
    """

    __tablename__ = "feedback_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    brief_id: Mapped[int] = mapped_column(ForeignKey("briefs.id"), index=True)
    creator_id: Mapped[int] = mapped_column(ForeignKey("creators.id"), index=True)
    #: Which batch the client was reacting to. NULL for unsolicited feedback.
    run_id: Mapped[int | None] = mapped_column(ForeignKey("recommendation_runs.id"))

    action: Mapped[str] = mapped_column(String(30), index=True)  # FEEDBACK_ACTIONS
    reason_code: Mapped[str | None] = mapped_column(String(60))
    reason_text: Mapped[str | None] = mapped_column(Text)

    actor: Mapped[str | None] = mapped_column(String(120))
    #: client | mango -- who performed it, so client-visible history can be
    #: filtered to the client's own actions.
    actor_side: Mapped[str] = mapped_column(String(20), default="client")
    rule_version: Mapped[str | None] = mapped_column(String(40))

    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=now, index=True)

    brief: Mapped[Brief] = relationship()
    creator: Mapped[Creator] = relationship()
    tasks: Mapped[list["InternalTask"]] = relationship(back_populates="feedback_event")


class InternalTask(Base):
    """Mango's operating surface: where a client action becomes real work.

    The BD system's ``ActionItem`` required a ``company_id``, so a question
    about a *creator* had nowhere to go. Here both links are optional and a
    task can hang off a feedback event, a creator, a quote, or nothing.

    INTERNAL ONLY -- no client serializer reads this table. A client sees the
    *effect* ("价格待 Mango 确认"), never the task.
    """

    __tablename__ = "internal_tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    kind: Mapped[str] = mapped_column(String(40), index=True)  # TASK_KINDS
    status: Mapped[str] = mapped_column(String(20), default="open", index=True)

    brief_id: Mapped[int | None] = mapped_column(ForeignKey("briefs.id"))
    creator_id: Mapped[int | None] = mapped_column(ForeignKey("creators.id"))
    quote_id: Mapped[int | None] = mapped_column(ForeignKey("quotes.id"))
    feedback_event_id: Mapped[int | None] = mapped_column(ForeignKey("feedback_events.id"))

    title: Mapped[str] = mapped_column(Text)
    detail: Mapped[str | None] = mapped_column(Text)
    owner: Mapped[str | None] = mapped_column(String(120))
    due_date: Mapped[dt.date | None] = mapped_column(Date)
    outcome_notes: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=now)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime, default=now, onupdate=now)
    closed_at: Mapped[dt.datetime | None] = mapped_column(DateTime)

    feedback_event: Mapped[FeedbackEvent | None] = relationship(back_populates="tasks")
    creator: Mapped[Creator | None] = relationship()
    quote: Mapped[Quote | None] = relationship()


class V3Session(Base):
    """v3 客户会话：偏好、重点圈层、保留名单。

    会话不是主数据，是客户在一条链接里的工作状态。它刻意**不引用** ``Brief``：
    v3 的默认路径是没有 Brief 码的陌生访客，先选完再说；有 Brief 码时把预填值
    写进 ``prefs``，两条路径共用同一张表。

    整份状态存成 JSON 而不是拆成列，因为它的形状由前端 store 决定（v3 README
    「State Management」），列化只会让每次前端调整都变成一次迁移。字段名与前端
    store 保持一致，对接时不需要翻译表。

    ``submitted_at`` 一旦写入就不再接受 ``PUT`` —— 客户提交的是一份 Mango 要去
    履约的名单，提交后被静默改写是合同问题，不是数据问题。
    """

    __tablename__ = "v3_sessions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    brief_code: Mapped[str | None] = mapped_column(String(64), index=True)

    #: {group, domains[], markets[], languages[], audiences[], budget, brand[]}
    prefs: Mapped[str | None] = mapped_column(Text)
    #: 重点圈层 id，最多 3。只重排不过滤。
    focus: Mapped[str | None] = mapped_column(Text)
    #: 客户手填、待 BD 核查的目标人物 [{name, hint}]
    custom: Mapped[str | None] = mapped_column(Text)
    #: {A: [{id, format}], B: [...]}
    plans: Mapped[str | None] = mapped_column(Text)
    plan: Mapped[str] = mapped_column(String(4), default="A")
    list_open: Mapped[bool] = mapped_column(Boolean, default=False)

    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=now)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime, default=now, onupdate=now)
    submitted_at: Mapped[dt.datetime | None] = mapped_column(DateTime)
