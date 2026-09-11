"""Observation layer — raw X follow-graph snapshots over time.

Feeds Signal Map's KOL–Root attention path (path 3): who does Mango's roster
collectively pay attention to, and which of those accounts follow back.

Why snapshots rather than a single graph table
----------------------------------------------
Three reasons, all specific to the Root graph:

* Root candidates shift as the roster and the vertical move; a single
  overwritten table loses that.
* A Root that **unfollows** a creator is a real event -- the attention path
  degraded -- and it is invisible without a prior observation to compare to.
* Re-collection must add evidence, not replace it.

X does not expose **when** a follow started, so every edge records when *we
observed* it -- never when it began.

``FollowEdge.first_seen_at`` therefore means "the first time Mango saw this
edge", which is a strictly weaker claim than "the follow began then". Any copy
derived from it must say so; treating the two as the same would manufacture a
timeline.

Direction discipline
--------------------
An edge is always ``source follows target``. The two directions answer
different questions and must never be conflated:

* ``KOL → follows → account`` — what our roster pays attention to. A **candidate
  generator**, and evidence of nothing else.
* ``Root → follows → KOL`` — the creator can enter that Root's feed. This is
  the real path-3 attention signal, and it belongs in ``AttentionSignal``,
  not here.

This module stores the raw observation for both. Promoting an observation to
an ``AttentionSignal`` is a separate, deliberate step.
"""

from __future__ import annotations

import datetime as dt

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
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .models import Base, now

#: What a collection run pulled for its observer.
SNAPSHOT_KINDS = ("following", "followers")

#: Why an account is in the table at all. An account can be several of these.
ACCOUNT_ROLES = (
    "roster",           # a creator Mango can actually buy
    "root_candidate",   # surfaced by roster-centrality, not yet reviewed
    "root",             # human-reviewed and accepted as a Root
    "observed",         # merely seen as an edge endpoint
)

#: Where a candidate stands in human review.
#:
#: ``rejected`` has to be storable. Without it a candidate a human has already
#: dismissed returns to the queue on every run, and -- worse -- its follow
#: signals keep counting toward a creator's 注意力路径 as though nobody had
#: looked. "Reviewed and turned down" is a different state from "not yet
#: reviewed", and only one of them is evidence of anything.
ROOT_REVIEW_STATUSES = ("pending", "accepted", "rejected")

#: Root typing. Overlaps ``normalize.AUDIENCE_TYPES`` where the two genuinely
#: describe the same thing from opposite ends -- ``investor``/``investors``,
#: ``enterprise_buyer``/``enterprise`` -- so "this creator reaches investors"
#: joins up with "this Root is an investor".
#:
#: The vocabularies are **not** identical and should not be forced to be:
#: ``media`` and ``community_leader`` describe what a Root *is*, not an
#: audience segment Mango sells to, so they have no counterpart by design.
ROOT_TYPES = (
    "industry_leader",   # 行业大佬 -- cross-vertical recognition
    "vertical_expert",   # 垂直专家 -- deep authority in one vertical
    "investor",          # 投资人
    "media",             # 媒体节点
    "community_leader",  # 社区领袖
    "enterprise_buyer",  # 企业决策者
    "project",           # a product/company account, not a person
    "unknown",
)

ROOT_TYPE_LABELS_ZH = {
    "industry_leader": "行业大佬", "vertical_expert": "垂直专家",
    "investor": "投资人", "media": "媒体节点", "community_leader": "社区领袖",
    "enterprise_buyer": "企业决策者", "project": "项目/产品账号", "unknown": "待分类",
}


class XAccount(Base):
    """One X account we have seen, whatever the reason.

    Keyed by ``rest_id`` rather than handle: handles are renamed, and a rename
    would otherwise silently split one account into two -- which is also the
    exact event FrontRun-style discovery wants to *detect*, so it is recorded
    (``handle_history``) rather than lost.
    """

    __tablename__ = "x_accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    rest_id: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    handle: Mapped[str | None] = mapped_column(String(120), index=True)
    display_name: Mapped[str | None] = mapped_column(String(255))
    bio: Mapped[str | None] = mapped_column(Text)

    followers: Mapped[int | None] = mapped_column(Integer, index=True)
    following: Mapped[int | None] = mapped_column(Integer)
    #: How many public lists include this account. A better authority proxy
    #: than follower count -- being listed is a deliberate curation act,
    #: whereas following is cheap.
    listed_count: Mapped[int | None] = mapped_column(Integer)
    verified: Mapped[bool | None] = mapped_column(Boolean)

    #: Comma-separated ``ACCOUNT_ROLES``.
    roles: Mapped[str | None] = mapped_column(String(120), index=True)

    # --- human review: decision ---------------------------------------------
    #: Set only for accounts a human reviewed and accepted as a Root.
    root_type: Mapped[str | None] = mapped_column(String(30), index=True)
    root_verticals: Mapped[str | None] = mapped_column(String(255))
    root_review_status: Mapped[str] = mapped_column(
        String(20), default="pending", index=True
    )
    root_reviewed_at: Mapped[dt.datetime | None] = mapped_column(DateTime)
    root_reviewed_by: Mapped[str | None] = mapped_column(String(120))
    root_note: Mapped[str | None] = mapped_column(Text)

    # --- machine suggestion: never the decision -----------------------------
    #: What a rule or a model proposed, kept in its **own** column so it can
    #: never be mistaken for ``root_type``. Same discipline as
    #: ``market_region`` (derived) vs ``audience_markets`` (measured): the day
    #: a suggestion writes into the decision column is the day nobody can tell
    #: which Roots a human actually accepted.
    root_suggested_type: Mapped[str | None] = mapped_column(String(30))
    #: What produced the suggestion, and the quoted content behind it.
    root_suggestion_basis: Mapped[str | None] = mapped_column(Text)
    root_suggested_by: Mapped[str | None] = mapped_column(String(120))

    #: Link back to the sellable roster when this account is one of ours.
    creator_id: Mapped[int | None] = mapped_column(ForeignKey("creators.id"), index=True)

    #: 头像 URL。看着像装饰，其实不是：客户名单上每张卡都空着一个圆圈时，
    #: 整份交付读起来就像没做完 —— 而这个字段在我们已经付过费的响应里就有。
    avatar_url: Mapped[str | None] = mapped_column(String(500))

    #: 近期内容表现。粉丝数说明的是历史积累，播放中位数说明的是**现在还有多少
    #: 人在看** —— 两者经常差一个量级，而客户判断值不值这个价看的是后者。
    #:
    #: 用中位数不用平均数：一条爆款会把均值抬到看不出常态。
    median_views: Mapped[float | None] = mapped_column(Float)
    recent_posts_counted: Mapped[int | None] = mapped_column(Integer)
    #: 最近一条被计入的原创帖的日期。它同时回答「还活跃吗」—— 半年前的中位数
    #: 和上周的中位数不是同一种信息。
    latest_post_at: Mapped[dt.date | None] = mapped_column(Date)
    posts_observed_at: Mapped[dt.datetime | None] = mapped_column(DateTime)

    #: Renames, as observed. A rename is itself a discovery signal.
    handle_history: Mapped[str | None] = mapped_column(Text)
    bio_changed_at: Mapped[dt.datetime | None] = mapped_column(DateTime)

    first_seen_at: Mapped[dt.datetime] = mapped_column(DateTime, default=now)
    profile_refreshed_at: Mapped[dt.datetime | None] = mapped_column(DateTime)

    def has_role(self, role: str) -> bool:
        return role in {r.strip() for r in (self.roles or "").split(",") if r.strip()}


class FollowSnapshot(Base):
    """One collection run against one observer account.

    Records what was *attempted* as well as what was retrieved, because a
    truncated pull makes absence meaningless: if only 3 of 12 pages were read,
    "no edge to X" says nothing at all. ``is_complete`` and
    ``coverage_limitation`` exist so a later query can refuse to draw that
    conclusion.
    """

    __tablename__ = "follow_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    observer_id: Mapped[int] = mapped_column(ForeignKey("x_accounts.id"), index=True)
    kind: Mapped[str] = mapped_column(String(20), index=True)  # SNAPSHOT_KINDS

    collected_at: Mapped[dt.datetime] = mapped_column(DateTime, default=now, index=True)
    collected_date: Mapped[dt.date] = mapped_column(Date, index=True)

    edge_count: Mapped[int] = mapped_column(Integer, default=0)
    pages_fetched: Mapped[int] = mapped_column(Integer, default=0)
    #: False when a page cap or an error stopped the pull short.
    is_complete: Mapped[bool] = mapped_column(Boolean, default=False)
    coverage_limitation: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)

    observer: Mapped[XAccount] = relationship()


class FollowEdge(Base):
    """``source follows target``, with the window over which we observed it.

    One row per pair rather than per snapshot: storing every snapshot's full
    edge list would grow without bound, while first/last-seen carries the same
    delta information.

    * newly followed  -> ``first_seen_snapshot_id`` is the observer's latest run
    * unfollowed      -> ``last_seen_snapshot_id`` is older than that run

    ``first_seen_at`` is when **Mango first observed** the edge, not when the
    follow began -- X does not expose the latter, and conflating them would
    invent a date.
    """

    __tablename__ = "follow_edges"
    __table_args__ = (UniqueConstraint("source_id", "target_id", name="uq_follow_edge"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("x_accounts.id"), index=True)
    target_id: Mapped[int] = mapped_column(ForeignKey("x_accounts.id"), index=True)

    first_seen_at: Mapped[dt.datetime] = mapped_column(DateTime, default=now, index=True)
    last_seen_at: Mapped[dt.datetime] = mapped_column(DateTime, default=now, index=True)
    times_seen: Mapped[int] = mapped_column(Integer, default=1)

    first_seen_snapshot_id: Mapped[int | None] = mapped_column(ForeignKey("follow_snapshots.id"))
    last_seen_snapshot_id: Mapped[int | None] = mapped_column(ForeignKey("follow_snapshots.id"))

    #: Set when a later complete snapshot no longer contained this edge.
    #: Only trustworthy when that snapshot was complete -- see FollowSnapshot.
    disappeared_at: Mapped[dt.datetime | None] = mapped_column(DateTime)

    source: Mapped[XAccount] = relationship(foreign_keys=[source_id])
    target: Mapped[XAccount] = relationship(foreign_keys=[target_id])


class RosterCentrality(Base):
    """How many of Mango's own creators follow a given account.

    The stage-1 output: a data-driven Root candidate ranking grounded in the
    vertical our inventory actually lives in, rather than a hand-guessed list
    of famous people our roster has no relation to.

    **Known bias, stated so nobody forgets it:** this is centrality within a
    *purchased roster*, not within the industry. It reflects who Mango happened
    to sign. ``in_vertical_followers`` narrows it -- counting only roster
    members whose own verticals match -- but cannot remove the bias.
    """

    __tablename__ = "roster_centrality"
    __table_args__ = (UniqueConstraint("account_id", "computed_date", name="uq_centrality_day"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("x_accounts.id"), index=True)

    #: Roster members following this account.
    roster_followers: Mapped[int] = mapped_column(Integer, default=0, index=True)
    #: Of those, the ones sharing a vertical with it -- a cheap purity filter,
    #: after XHunt's "≥40% in-niche" idea: a generic mega-account should not
    #: read as an insider just because many people follow it.
    in_vertical_followers: Mapped[int] = mapped_column(Integer, default=0)
    #: roster_followers / roster size actually collected, so the number stays
    #: comparable as collection coverage grows.
    roster_share: Mapped[float | None] = mapped_column(Float)

    #: roster_followers / the account's **global** follower count — the share
    #: of this account's whole audience that is Mango's roster.
    #:
    #: This is the discriminator. Raw counts conflate "famous" with "central to
    #: our circle": @elonmusk is followed by 62% of the roster and by 241M
    #: people, so the follow means nothing; @huang_song_ is followed by 40% of
    #: the roster out of a total audience of 6,711, so the roster is ~1% of
    #: everyone who follows them. Same raw rank, 23,000x apart in signal.
    audience_concentration: Mapped[float | None] = mapped_column(Float, index=True)
    #: Global follower count at compute time, so concentration stays auditable
    #: after the account grows.
    global_followers: Mapped[int | None] = mapped_column(Integer)

    computed_date: Mapped[dt.date] = mapped_column(Date, index=True)
    roster_size: Mapped[int] = mapped_column(Integer, default=0)

    account: Mapped[XAccount] = relationship()
