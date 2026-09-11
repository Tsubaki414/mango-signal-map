"""Root 人工审核 — turning 249 candidates into a reviewed Root list.

Stage 2 collected ``Root → follows → our creator`` for every account the
roster-centrality ranking surfaced. That produced 249 accounts with at least
one real edge, and **not one of them has been looked at by a person**. Until
someone does, the 注意力路径 axis is reporting "被 @X 关注" without anyone
having established that @X is a Root at all.

Why the candidate list cannot be trusted as-is
----------------------------------------------
Inspecting the 249 shows two populations that the ranking metric cannot tell
apart, because it was never designed to:

* **Real Roots** — @AndrewYNg, @garrytan, @levelsio, @ClementDelangue,
  @Polymarket. Each follows 1–5 of our creators out of thousands.
* **Peer accounts inside the same growth network** — @Trisha_Techie follows
  **71 of our creators out of the 799 accounts it follows at all**. @Faazsh:
  58 of 618. That is ~9% of an entire follow list being one agency's roster.

Both look identical on follower count, and nearly identical on listed-count
rate (@AlfaizAliX 21.3 per 1k beats @AndrewYNg's 11.0). A single composite
score would therefore rank a mutual-follow pod above a Turing-award laureate
while showing the client one confident number — the exact failure this product
forbids.

So this module computes **named, separately-readable flags**, each carrying the
figure behind it, and hands them to a person. Nothing here decides anything.

The one number that does separate them
--------------------------------------
``roster_share_of_following`` — of everything this account follows, what share
is Mango's roster. Across the observed candidates it runs ~0.08% for genuine
industry figures and ~9% for pod accounts: a hundredfold gap with a plain
reading. It is still only a flag. A genuine niche expert in a small vertical
could legitimately follow many of our creators, so a human decides.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from .models import AttentionSignal
from .observation_models import (
    ROOT_REVIEW_STATUSES,
    ROOT_TYPE_LABELS_ZH,
    ROOT_TYPES,
    RosterCentrality,
    XAccount,
)

#: Above this share of an account's whole follow list being Mango's roster, the
#: account is behaving like a member of the roster's own network rather than an
#: outside Root. Set from the observed gap (peers ~9%, real Roots ~0.1%) and
#: deliberately placed nearer the peers, so the flag under-fires: wrongly
#: flagging a real Root wastes a reviewer's minute, wrongly clearing a pod puts
#: a fake attention path in front of a client.
PEER_NETWORK_SHARE = 0.03

#: The complementary ratio: what share of **Mango's roster** this account has
#: swept up. Needed because the first one alone misses the biggest offender in
#: the real data — @MagnaDing follows 84 of our 188 creators, but out of a
#: 3,988-account follow list, so its share-of-following is a harmless-looking
#: 2.1%. Reading only that ratio would have cleared the single most
#: roster-saturated account in the queue.
#:
#: The two ratios catch different behaviours and neither subsumes the other:
#: a small follow list dense with our creators, versus a large one that swept
#: the roster up wholesale. Both are 同圈互关 signatures; a Root that reaches an
#: outside audience is neither.
ROSTER_SWEEP_SHARE = 0.15

#: Bio phrases that advertise a growth or ghostwriting service. Their presence
#: says the account trades in reach, which is what a *creator* does -- so it is
#: evidence against the account being an audience Root, not merely noise.
_GROWTH_SERVICE_MARKERS = (
    "ghostwriter", "ghostwriting", "dm for collab", "dm to collab", "dm for promo",
    "grow your", "growth strategist", "let's collab", "collab dm", "promote your",
    "shoutout", "帮推", "互推",
)

#: Bio phrases that identify a Root's kind. Only structural, unambiguous ones
#: live here: "who is this person really" is a semantic judgment that belongs
#: to a human or, at most, to the LLM pass under its own guardrails.
_TYPE_MARKERS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("investor", ("general partner", "managing partner", "venture", "vc ", " vc", "angel investor",
                  "投资人", "partner at", "invest in", "backing founders")),
    ("media", ("newsletter", "podcast", "daily news", "we cover", "publication", "magazine",
               "reporting on", "media company")),
    ("community_leader", ("community", "founder of the", "we host", "meetup", "dao ", "社区")),
    ("enterprise_buyer", ("cto at", "cio at", "head of engineering", "head of marketing",
                          "vp of", "director of")),
)


@dataclass(frozen=True)
class ReviewFlag:
    """One readable observation about a candidate, with its figure attached.

    ``reading`` is deliberately three-valued and never summed. A reviewer sees
    "supports" and "against" side by side and weighs them; collapsing that into
    a score is what makes a pod outrank a laureate.
    """

    key: str
    label_zh: str
    reading: str  # supports_root | against_root | context
    detail: str

    def as_dict(self) -> dict:
        return {
            "key": self.key,
            "label_zh": self.label_zh,
            "reading": self.reading,
            "detail": self.detail,
        }


@dataclass
class RootCandidate:
    """A candidate plus everything a reviewer needs to judge it in one screen."""

    account: XAccount
    creators_followed: int
    creator_names: list[str]
    interaction_count: int
    flags: list[ReviewFlag]
    suggested_type: str | None
    suggestion_basis: str | None

    @property
    def roster_share_of_following(self) -> float | None:
        if not self.account.following:
            return None
        return self.creators_followed / self.account.following


def _pct(value: float) -> str:
    return f"{value * 100:.2f}%" if value < 0.01 else f"{value * 100:.1f}%"


def review_flags(
    account: XAccount,
    creators_followed: int,
    interaction_count: int,
    roster_size: int | None = None,
) -> list[ReviewFlag]:
    """Everything observable about this candidate, stated separately.

    Order is presentation order, not priority: a reviewer reads all of them.
    """
    flags: list[ReviewFlag] = []
    following = account.following
    followers = account.followers

    share = creators_followed / following if following else None
    sweep = creators_followed / roster_size if roster_size else None
    peerish = (share is not None and share >= PEER_NETWORK_SHARE) or (
        sweep is not None and sweep >= ROSTER_SWEEP_SHARE
    )

    if sweep is not None and sweep >= ROSTER_SWEEP_SHARE:
        flags.append(ReviewFlag(
            "roster_sweep", "已扫过大半名单", "against_root",
            f"关注了我们 {roster_size} 位创作者中的 {creators_followed} 位"
            f"（{_pct(sweep)}）——外部 Root 不会成规模地关注同一家机构的名单",
        ))
    if share is not None and share >= PEER_NETWORK_SHARE:
        flags.append(ReviewFlag(
            "peer_network", "疑似同圈互关", "against_root",
            f"该账号关注的 {following:,} 个账号里有 {creators_followed} 个是我们的创作者"
            f"（{_pct(share)}）——比例接近互关网络，而非外部关注者",
        ))
    if share is not None and not peerish:
        flags.append(ReviewFlag(
            "selective_follow", "关注具有选择性", "supports_root",
            f"关注 {following:,} 个账号，其中我们的创作者 {creators_followed} 个"
            f"（{_pct(share)}）"
            + (f"，占名单 {_pct(sweep)}" if sweep is not None else ""),
        ))

    if followers and account.listed_count is not None:
        per_1k = account.listed_count / followers * 1000
        # Being added to a public list is a deliberate curation act by a
        # stranger, which is why it beats follower count as an authority proxy.
        # It does *not* separate these two populations on its own -- stated
        # here as context so nobody re-derives that the hard way.
        flags.append(ReviewFlag(
            "curation", "被收录进公开列表", "context",
            f"{account.listed_count:,} 个公开列表收录（每千粉 {per_1k:.1f}）",
        ))

    if followers is not None:
        flags.append(ReviewFlag(
            "audience_size", "粉丝规模", "context", f"{followers:,} 粉丝",
        ))

    bio = (account.bio or "").lower()
    hit = next((m for m in _GROWTH_SERVICE_MARKERS if m in bio), None)
    if hit is not None:
        flags.append(ReviewFlag(
            "growth_service", "简介含涨粉/代写服务", "against_root",
            f'简介出现「{hit}」——该账号在售卖曝光，更像同行创作者而非受众 Root',
        ))

    if interaction_count:
        flags.append(ReviewFlag(
            "interaction", "存在互动证据", "supports_root",
            f"除关注外另有 {interaction_count} 条公开互动记录",
        ))

    if creators_followed == 1:
        # Worth saying out loud: one edge is the weakest possible basis, and a
        # reviewer should not read a single follow as a considered choice.
        flags.append(ReviewFlag(
            "single_edge", "仅一条关注边", "context",
            "只观察到关注我们 1 位创作者——证据面很窄",
        ))

    return flags


def suggest_type(account: XAccount, flags: list[ReviewFlag]) -> tuple[str | None, str | None]:
    """A conservative rule suggestion, with the phrase that produced it.

    Returns ``(None, None)`` rather than guessing. It only claims the types a
    bio states structurally -- an investor says "General Partner", a media
    account says "newsletter". It deliberately never proposes
    ``industry_leader`` or ``vertical_expert``: telling those apart requires
    knowing the person's standing in a field, which no keyword can see and
    which the LLM pass handles under its own guardrails.
    """
    bio = (account.bio or "").lower()
    if not bio:
        return None, None

    if any(f.key == "growth_service" for f in flags):
        # An account selling reach is a creator. Saying so is a real
        # suggestion, and the most common correct verdict in this queue.
        return None, None

    for root_type, markers in _TYPE_MARKERS:
        for marker in markers:
            if marker in bio:
                excerpt = _excerpt(account.bio or "", marker)
                return root_type, f'规则：简介出现「{excerpt}」'
    return None, None


def _excerpt(text: str, marker: str, width: int = 40) -> str:
    """The marker in context, so a reviewer sees what actually matched."""
    index = text.lower().find(marker)
    if index < 0:
        return marker
    start = max(0, index - width // 2)
    snippet = re.sub(r"\s+", " ", text[start : index + len(marker) + width // 2]).strip()
    return f"…{snippet}…" if start > 0 else snippet


def candidate_rows(
    session: Session,
    *,
    status: str = "pending",
    limit: int = 100,
    min_creators: int = 1,
    rest_ids: list[str] | None = None,
) -> list[RootCandidate]:
    """Candidates with observed edges, ranked for review value.

    Ranked by how many of Mango's creators the account follows, **descending**,
    which puts the peer-network accounts first on purpose: they are the bulk of
    the queue and the fastest to clear, and every one rejected removes a false
    attention path from a client-facing axis.
    """
    counts = (
        select(
            AttentionSignal.source_node.label("node"),
            func.count(func.distinct(AttentionSignal.source_creator_id)).label("creators"),
            func.sum(
                case((AttentionSignal.signal_type != "follow", 1), else_=0)
            ).label("interactions"),
        )
        .group_by(AttentionSignal.source_node)
        .subquery()
    )

    query = (
        select(XAccount, counts.c.creators, counts.c.interactions)
        .join(counts, counts.c.node == XAccount.rest_id)
        .where(counts.c.creators >= min_creators)
        .order_by(counts.c.creators.desc(), XAccount.followers.desc())
        .limit(limit)
    )
    if status:
        query = query.where(XAccount.root_review_status == status)
    if rest_ids is not None:
        query = query.where(XAccount.rest_id.in_(rest_ids))

    roster_size = roster_account_count(session)
    rows: list[RootCandidate] = []
    for account, creators, interactions in session.execute(query).all():
        names = _creator_names(session, account.rest_id)
        flags = review_flags(account, creators, interactions or 0, roster_size)
        suggested, basis = account.root_suggested_type, account.root_suggestion_basis
        rule_type, rule_basis = suggest_type(account, flags)

        if suggested is None and basis is None:
            suggested, basis = rule_type, rule_basis
        elif suggested is None and basis is not None:
            # A stored basis with no type means the model actively judged this
            # account **not** a Root. That verdict is the most common one in
            # this queue and the most useful, so it is shown as a flag rather
            # than left as an absent suggestion a reviewer could read as
            # "nothing known".
            flags.append(ReviewFlag(
                "suggested_not_root", "机器判断为非 Root", "against_root", basis,
            ))
        elif rule_type is not None and rule_type != suggested:
            # Two methods, two answers. Neither wins automatically -- the flag
            # exists to send a reviewer's attention here, which is exactly
            # where it is worth spending.
            flags.append(ReviewFlag(
                "suggestion_conflict", "两种判断不一致", "context",
                f"规则建议「{ROOT_TYPE_LABELS_ZH.get(rule_type, rule_type)}」，"
                f"模型建议「{ROOT_TYPE_LABELS_ZH.get(suggested, suggested)}」；{rule_basis}",
            ))
        rows.append(
            RootCandidate(
                account=account,
                creators_followed=creators,
                creator_names=names,
                interaction_count=interactions or 0,
                flags=flags,
                suggested_type=suggested,
                suggestion_basis=basis,
            )
        )
    return rows


def roster_account_count(session: Session) -> int:
    """How many of Mango's creators the follow graph could possibly match.

    The denominator for ``roster_sweep``. It is the **collected** roster, not
    the priced roster: an account that swept 84 of the 188 accounts we actually
    look for has done so regardless of which of them carry a quote.
    """
    return session.scalar(
        select(func.count()).select_from(XAccount).where(
            XAccount.roles.like("%roster%"), XAccount.creator_id.is_not(None)
        )
    ) or 0


def _creator_names(session: Session, rest_id: str, limit: int = 12) -> list[str]:
    """Which of our creators this account follows — the edges under review."""
    from .models import Creator

    return list(
        session.scalars(
            select(Creator.display_name)
            .join(AttentionSignal, AttentionSignal.source_creator_id == Creator.id)
            .where(AttentionSignal.source_node == rest_id)
            .distinct()
            .limit(limit)
        ).all()
    )


def decide(
    session: Session,
    account: XAccount,
    *,
    actor: str,
    status: str,
    root_type: str | None = None,
    verticals: str | None = None,
    note: str | None = None,
) -> XAccount:
    """Record a human's Root verdict. The only writer of ``root_type``.

    Accepting **requires** a type: an accepted Root with no type cannot answer
    "which 圈层 does this reach", which is the entire reason the client picks
    Roots at all. Rejecting requires nothing but a reason worth keeping.
    """
    if status not in ROOT_REVIEW_STATUSES:
        raise ValueError(f"status must be one of {ROOT_REVIEW_STATUSES}")
    if status == "accepted":
        if root_type not in ROOT_TYPES:
            raise ValueError(f"accepting a Root requires root_type in {ROOT_TYPES}")
        if root_type == "unknown":
            raise ValueError("accepting a Root requires a real type, not 'unknown'")

    account.root_review_status = status
    account.root_reviewed_by = actor
    account.root_reviewed_at = dt.datetime.now(dt.UTC).replace(tzinfo=None)
    account.root_note = note

    roles = {r.strip() for r in (account.roles or "").split(",") if r.strip()}
    if status == "accepted":
        account.root_type = root_type
        account.root_verticals = verticals
        roles.discard("root_candidate")
        roles.add("root")
    else:
        # A rejected candidate keeps no type at all. Leaving a stale one behind
        # would let a later query read it as accepted.
        account.root_type = None
        account.root_verticals = None
        roles.discard("root")
        roles.discard("root_candidate")
    account.roles = ",".join(sorted(roles)) or None
    return account


def accepted_root_nodes(session: Session) -> dict[str, XAccount]:
    """``{rest_id: account}`` for Roots a human accepted. Used by the axis."""
    rows = session.scalars(
        select(XAccount).where(
            XAccount.root_review_status == "accepted", XAccount.root_type.is_not(None)
        )
    ).all()
    return {account.rest_id: account for account in rows}


def rejected_root_nodes(session: Session) -> set[str]:
    """Rest ids a human has turned down. Their signals stop counting."""
    return set(
        session.scalars(
            select(XAccount.rest_id).where(XAccount.root_review_status == "rejected")
        ).all()
    )


def review_summary(session: Session) -> dict:
    """Progress, and what is left — the honest denominator for the axis.

    Reported because 注意力路径 currently rests on unreviewed candidates, and a
    reader is entitled to know how much of it a person has stood behind.
    """
    observed = select(AttentionSignal.source_node).distinct().subquery()
    base = (
        select(XAccount.root_review_status, func.count())
        .join(observed, observed.c.source_node == XAccount.rest_id)
        .group_by(XAccount.root_review_status)
    )
    by_status = {status: count for status, count in session.execute(base).all()}

    by_type = {
        root_type: count
        for root_type, count in session.execute(
            select(XAccount.root_type, func.count())
            .where(XAccount.root_review_status == "accepted")
            .group_by(XAccount.root_type)
        ).all()
    }

    total = sum(by_status.values())
    reviewed = total - by_status.get("pending", 0)
    return {
        "candidates_with_edges": total,
        "reviewed": reviewed,
        "pending": by_status.get("pending", 0),
        "accepted": by_status.get("accepted", 0),
        "rejected": by_status.get("rejected", 0),
        "accepted_by_type": {
            key: {"count": value, "label_zh": ROOT_TYPE_LABELS_ZH.get(key, key)}
            for key, value in sorted(by_type.items())
        },
        # Stated plainly: while this is 0, every 注意力路径 line in the product
        # rests on an unreviewed candidate.
        "note": (
            "未审核的候选不是 Root，其关注信号只能作为研究线索"
            if by_status.get("pending")
            else "全部候选已审核"
        ),
    }


def centrality_for(session: Session, account_id: int) -> RosterCentrality | None:
    """Latest centrality row, for showing how the candidate was surfaced."""
    return session.scalars(
        select(RosterCentrality)
        .where(RosterCentrality.account_id == account_id)
        .order_by(RosterCentrality.computed_date.desc())
        .limit(1)
    ).first()
