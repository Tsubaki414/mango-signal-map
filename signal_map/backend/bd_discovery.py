"""可 BD 候选池的实时构建 —— 池子是查询结果，不是一份名单。

池子 = 能买的人。共同关注是**资格标记**，不是排序依据
----------------------------------------------------
这一条是审计之后改过来的，前一版是错的，值得写清楚为什么。

前一版把「被所选名人共同关注的账号」当成候选池的第一来源，并按共同关注人数
排序。用真实数据跑出来，前 20 名是 natfriedman、paulg、garrytan、Box CEO、
Stripe CEO、Shopify CEO、reidhoffman、sundarpichai、Hinton、OpenAI、
AnthropicAI —— **一个能买的都没有**。原因是结构性的：**名人互相关注**，所以
任何「有多少名人关注他」的排名，排出来的都是名人。按关注选择性加权
（@gdb 关注 8 人 vs @pmarca 关注 32,758 人）重跑，前 25 名几乎没变。这不是
调参问题。

而且一条关注本身很弱：@pmarca 关注 32,758 个账号，他关注你并不代表他会看你。

所以现在的结构是：

* **池子由「能不能买」定义** —— 已在库的创作者，加上公开简介里带商业信号
  （商务邮箱、接单声明、合作表单）的账号。
* **共同关注只是打在他们身上的一个标记**：这个人的内容能进哪些目标的信息流。
  它是资格证明和排序里的次要因素，不是主排序。

三条来源，每次调用现算
----------------------
1. **已在库、但还没有报价的对象**（``Creator`` 无 priced quote）。身份、领域、
   受众证据都齐，只差商务动作。已有报价的走客户推荐那一块。
2. **观察层里带商业信号的账号**（简介字面写着商务邮箱 / 接单声明）。这是
   「可买」的最弱但可核验的证据，6,105 个有简介的账号里有 268 个。
3. **外部导入的名单**（``BDCandidate``：老板给的 sample、建联工作表）。附带的
   评分和分层一律 ``unverified``，不参与任何判断。

三者数据完备度差得远，所以每个派生字段都带 basis。观察层账号只有一条公开
简介，那就据实说「依据：公开简介」，而不是假装和已归一的对象一样可靠。

两种关注信号不能混
------------------
* ``target_followed``：**客户选的目标人物**关注了这个账号。是资格标记，方向
  正确，但**只是门槛** —— 单独一条关注不能读成关系，要靠互动证据升级。
* ``roster_centrality``：**我们自己的创作者**关注了这个账号。**不是**关系
  证据，只说明 Mango 签的人在看谁。默认不进池子。
"""

from __future__ import annotations

import re

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from . import root_review
from .bd_models import BDCandidate, CommercialSignal
from .bd_screening import (
    CandidateView,
    DomainPreferences,
    collected_target_nodes,
    signals_from_bio,
)
from .models import Creator, Quote
from .normalize import (
    _VERTICAL_TOKENS,
    derive_audience_types,
    derive_market_region,
)
from .observation_models import FollowEdge, FollowSnapshot, RosterCentrality, XAccount

#: 观察层账号进入池子的门槛：至少这么多位自家创作者关注过它。1 条边说明不了
#: 任何事，而 12,226 个账号全量进池子只会把有意义的候选淹掉。
MIN_ROSTER_FOLLOWERS = 3

#: SQLite 的 ``IN (?, ?, ...)`` 参数上限。62 位名人的关注列表并起来有十万个
#: 账号，一次塞进一个 IN 子句会直接报 "too many SQL variables" —— 这是那种
#: 数据量小的时候永远不会出现、上了真实规模才炸的错误。
_SQL_PARAM_LIMIT = 900


def _in_chunks(values: list):
    """把一个长列表切成能塞进 ``IN`` 的几段。"""
    items = list(values)
    for start in range(0, len(items), _SQL_PARAM_LIMIT):
        yield items[start : start + _SQL_PARAM_LIMIT]

#: 观察层现场推导 verticals 时用的词表。复用 ``normalize._VERTICAL_TOKENS``
#: 的键，但匹配方式不同：那边按整格分类词匹配，这边要在一段自由文本里找词，
#: 所以必须加词边界 —— 否则 "ai" 会在 "email"、"chair"、"detail" 里命中。
_VERTICAL_BIO_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = tuple(
    (re.compile(rf"(?<![A-Za-z]){re.escape(token)}(?![A-Za-z])", re.IGNORECASE), vertical)
    for token, vertical in sorted(_VERTICAL_TOKENS.items(), key=lambda kv: -len(kv[0]))
    if len(token) >= 2
)


def verticals_from_bio(bio: str | None) -> tuple[tuple[str, ...], str | None]:
    """从公开简介里读领域，返回 ``(verticals, 命中的原文)``。

    只保留前三个：一个号什么都沾就等于什么都不是，长列表读起来像凑数。
    命中原文一并返回，卡片上要能显示「凭哪个词判断的」。
    """
    if not bio:
        return (), None
    found: list[str] = []
    quotes: list[str] = []
    for pattern, vertical in _VERTICAL_BIO_PATTERNS:
        if vertical in found:
            continue
        match = pattern.search(bio)
        if match:
            found.append(vertical)
            quotes.append(f'{vertical}:"{match.group(0)}"')
        if len(found) == 3:
            break
    return tuple(found), "; ".join(quotes) or None


# =============================================================================
# 来源 1 —— 已在库、还没有报价的对象
# =============================================================================


def _creator_view(creator: Creator, candidate: BDCandidate | None = None) -> CandidateView:
    account = None
    if creator.accounts:
        account = next(
            (a for a in creator.accounts if a.is_primary),
            max(creator.accounts, key=lambda a: a.followers or 0),
        )
    handle = (
        (creator.primary_handle or (account.handle if account else None) or creator.display_name)
        or ""
    ).lstrip("@")
    platform = account.platform if account else "Unknown"
    return CandidateView(
        key=f"{platform}:{handle.lower()}",
        platform=platform,
        handle=handle,
        display_name=creator.display_name,
        profile_url=account.profile_url if account else None,
        source_kind="creator",
        creator=creator,
        candidate=candidate,
        bio=account.bio if account else None,
        followers=account.followers if account else None,
        metrics_observed_at=account.metrics_observed_at if account else None,
        verticals=tuple(_csv(creator.verticals)),
        # 已在库对象的领域来自 BD 时期的分类格，不是从简介现读的。
        verticals_basis="categories",
        market_region=creator.market_region,
        market_region_basis=creator.market_region_basis,
        languages=tuple(_csv(creator.languages)),
        audience_types=tuple(_csv(creator.audience_types)),
        audience_basis=creator.audience_types_basis,
        audience_evidence=creator.audience_types_evidence,
        platforms=tuple(sorted({a.platform for a in creator.accounts})),
        object_kind=_object_kind_for_creator(creator, candidate),
        object_kind_basis=_object_kind_basis_for_creator(creator, candidate),
        target_group=candidate.target_group if candidate else None,
        discovery_basis={
            "source": "supply_record",
            "detail": "已在 Mango 供给库中，尚无可用报价",
            "creator_class": creator.creator_class,
            "creator_tier": creator.creator_tier,
        },
    )


def _object_kind_for_creator(creator: Creator, candidate: BDCandidate | None) -> str:
    if candidate is not None and candidate.object_kind != "unknown":
        return candidate.object_kind
    if creator.creator_tier == "media":
        return "media_channel"
    if creator.creator_tier == "non_creator":
        return "unknown"
    if creator.creator_tier in ("strategic", "distribution"):
        return "kol"
    return "unknown"


def _object_kind_basis_for_creator(creator: Creator, candidate: BDCandidate | None) -> str | None:
    if candidate is not None and candidate.object_kind != "unknown":
        return candidate.object_kind_basis
    return f"依据 BD 分类「{creator.creator_class}」（{creator.creator_tier}）"


def _csv(value: str | None) -> list[str]:
    return [part.strip() for part in (value or "").split(",") if part.strip()]


def creators_without_quote(session: Session) -> list[Creator]:
    """在库但没有可用报价的对象 —— 内部待建联名单的第一来源。"""
    priced = (
        select(func.count())
        .select_from(Quote)
        .where(Quote.creator_id == Creator.id, Quote.amount_usd.is_not(None))
        .scalar_subquery()
    )
    return list(
        session.scalars(
            select(Creator)
            .where(priced == 0)
            .options(
                selectinload(Creator.accounts),
                selectinload(Creator.quotes),
                selectinload(Creator.contacts),
                selectinload(Creator.sponsorships),
                selectinload(Creator.procurement_routes),
            )
        ).all()
    )


# =============================================================================
# 来源 2 —— 观察层发现
# =============================================================================


def _observed_view(
    account: XAccount,
    centrality: RosterCentrality | None,
    creators_followed: int,
    roster_size: int,
    candidate: BDCandidate | None = None,
) -> CandidateView:
    bio = account.bio
    profile_url = f"https://x.com/{account.handle}" if account.handle else None

    verticals, vertical_quote = verticals_from_bio(bio)
    market, market_basis = derive_market_region(languages=None, bio=bio)
    audiences, audience_basis, audience_evidence = derive_audience_types(
        ",".join(verticals) or None, bio=bio,
    )

    flags = root_review.review_flags(account, creators_followed, 0, roster_size)
    kind, kind_basis = _object_kind_for_account(account, flags)

    return CandidateView(
        key=f"X:{(account.handle or account.rest_id).lower()}",
        platform="X",
        handle=account.handle or account.rest_id,
        display_name=account.display_name,
        profile_url=profile_url,
        source_kind="observed",
        creator=None,
        candidate=candidate,
        x_account=account,
        bio=bio,
        followers=account.followers,
        metrics_observed_at=account.profile_refreshed_at,
        verticals=verticals,
        verticals_basis="bio",
        market_region=market,
        market_region_basis=market_basis,
        # 观察层没有内容语言字段。留空并让语言轴报 unknown，比拿简介语种冒充
        # 内容语言诚实 —— 后者已经被 market_region 用掉了。
        languages=(),
        audience_types=tuple(_csv(audiences)),
        audience_basis=audience_basis,
        audience_evidence=audience_evidence,
        platforms=("X",),
        object_kind=kind,
        object_kind_basis=kind_basis,
        discovery_basis={
            "source": "roster_centrality",
            "detail": (
                f"Mango 自有 {roster_size} 位创作者中有 {creators_followed} 位关注了该账号"
            ),
            "roster_followers": creators_followed,
            "audience_concentration": (
                round(centrality.audience_concentration, 6)
                if centrality and centrality.audience_concentration is not None else None
            ),
            "computed_date": centrality.computed_date.isoformat() if centrality else None,
            # CLAUDE.md 的教训：这个指标反映 Mango 签了谁，不是行业地位。
            "caveat": (
                "这是「我们的创作者关注了他」，不是「目标人物关注了他」；"
                "roster centrality 只反映 Mango 签下的人，不代表行业地位"
            ),
            "vertical_evidence": vertical_quote,
        },
        inline_signals=signals_from_bio(bio, source_url=profile_url),
        review_flags=[f.as_dict() for f in flags],
    )


def _object_kind_for_account(account: XAccount, flags: list) -> tuple[str, str | None]:
    """观察层账号是什么。没有把握就说 ``unknown``，不硬派身份。"""
    if account.root_review_status == "accepted" and account.root_type:
        return "target_person", f"已人工确认为 Root（{account.root_type}）"
    suggested = account.root_suggested_type
    if suggested == "media":
        return "media_channel", f"机器建议：媒体节点。{account.root_suggestion_basis or ''}".strip()
    if suggested == "project":
        return "unknown", (
            f"机器建议为项目/产品账号，不是个人创作者；需人工确认。"
            f"{account.root_suggestion_basis or ''}".strip()
        )
    if any(f.key == "growth_service" for f in flags):
        return "unknown", "简介在售卖曝光服务（涨粉/代写/互推），身份待人工确认"
    return "unknown", "身份尚未判定，按可投放对象走筛选，需人工确认"


#: 简介里出现这些就算「可买线索」。它们是**可核验的字面证据**，不是判断
#: 这个人「大概愿意接」—— 后者不属于任何自动规则。
_BUYABLE_SIGNAL_TYPES = frozenset({
    "business_email", "accepts_brand_work_statement", "booking_form",
    "media_kit", "public_rate_card", "partnership_page",
})


def buyable_accounts(session: Session) -> list[tuple[XAccount, list]]:
    """观察层里**有可买线索**的账号 —— 池子的定义，不是发现的副产品。

    门槛是「简介里字面写着一个商务入口」。这很弱，但有三个好处：可核验、
    有原文可引、而且它筛的是**这个人卖不卖**，不是**这个人有多有名**。
    6,105 个有简介的账号里剩下 268 个，是一个人能看完的量。

    没有简介的账号读不出任何东西，不进池子 —— 它们进采集缺口。
    """
    out: list[tuple[XAccount, list]] = []
    for account in session.scalars(
        select(XAccount).where(
            XAccount.bio.is_not(None),
            XAccount.bio != "",
            XAccount.creator_id.is_(None),
            XAccount.root_review_status != "rejected",
        )
    ):
        url = f"https://x.com/{account.handle}" if account.handle else None
        signals = signals_from_bio(account.bio, source_url=url)
        if any(s.signal_type in _BUYABLE_SIGNAL_TYPES for s in signals):
            out.append((account, signals))
    return out


# =============================================================================
# 共同关注 —— 打在池子成员身上的标记，不是池子来源
# =============================================================================


def target_followed_accounts(
    session: Session,
    target_ids: list[int],
    *,
    min_targets: int = 1,
) -> list[tuple[XAccount, list[dict]]]:
    """所选目标人物关注的账号，按有多少位目标关注排序。

    返回 ``(账号, [每条关注边的证据])``。每条边带观察时间和覆盖限制，因为
    这条边同时要当关系证据用 —— 一条没有观察时间、不说明名单是否采全的边，
    不能作为证据展示。

    这就是「名人共同关注」。``min_targets=2`` 时只留被至少两位目标关注的账号 ——
    但默认是 1，因为被一位 a16z GP 关注本身就是有意义的线索，而**要求被所有
    目标同时关注是错的**：那会把结果压成空集，规格里也写明不要求。

    ``disappeared_at`` 不为空的边被排除：一次完整的重采里没再出现，说明关注
    已经取消，那条注意力路径退化了。
    """
    if not target_ids:
        return []

    source = XAccount.__table__.alias("src")
    snapshot = FollowSnapshot.__table__.alias("snap")
    rows = session.execute(
        select(
            FollowEdge.target_id,
            source.c.handle,
            source.c.rest_id,
            FollowEdge.first_seen_at,
            FollowEdge.last_seen_at,
            snapshot.c.coverage_limitation,
        )
        .join(source, source.c.id == FollowEdge.source_id)
        .outerjoin(snapshot, snapshot.c.id == FollowEdge.last_seen_snapshot_id)
        .where(
            FollowEdge.source_id.in_(target_ids),
            # 一次完整重采里没再出现的边，说明关注已经取消，注意力路径退化了。
            FollowEdge.disappeared_at.is_(None),
        )
    ).all()
    if not rows:
        return []

    by_account: dict[int, list[dict]] = {}
    for account_id, handle, rest_id, first_seen, last_seen, limitation in rows:
        by_account.setdefault(account_id, []).append({
            "target_handle": handle,
            "target_rest_id": rest_id,
            "first_observed_at": first_seen.isoformat() if first_seen else None,
            "last_observed_at": last_seen.isoformat() if last_seen else None,
            "coverage_limitation": limitation,
        })

    wanted = [aid for aid, edges in by_account.items() if len(edges) >= min_targets]
    if not wanted:
        return []
    accounts: dict[int, XAccount] = {}
    for chunk in _in_chunks(wanted):
        for account in session.scalars(select(XAccount).where(XAccount.id.in_(chunk))):
            accounts[account.id] = account

    out: list[tuple[XAccount, list[dict]]] = []
    target_id_set = set(target_ids)
    for account_id in wanted:
        account = accounts.get(account_id)
        if account is None or account.root_review_status == "rejected":
            continue
        # 目标人物之间互相关注是常态，但目标不是投放对象，不进可 BD 池。
        if account.id in target_id_set:
            continue
        out.append((account, sorted(by_account[account_id], key=lambda e: e["target_handle"] or "")))
    out.sort(key=lambda item: -len(item[1]))
    return out


def observed_accounts(
    session: Session, *, min_roster_followers: int = MIN_ROSTER_FOLLOWERS,
) -> list[tuple[XAccount, RosterCentrality | None, int]]:
    """观察层里值得看的账号。

    三个排除条件，每个都有理由：已经关联到 creator 的（那是来源 1 的事）、
    人工已经否掉的（否掉了就不该回到队列）、以及自家 roster（我们已经有他们）。
    """
    latest = (
        select(
            RosterCentrality.account_id,
            func.max(RosterCentrality.computed_date).label("computed_date"),
        )
        .group_by(RosterCentrality.account_id)
        .subquery()
    )
    rows = session.execute(
        select(XAccount, RosterCentrality)
        .join(RosterCentrality, RosterCentrality.account_id == XAccount.id)
        .join(
            latest,
            (latest.c.account_id == RosterCentrality.account_id)
            & (latest.c.computed_date == RosterCentrality.computed_date),
        )
        .where(
            XAccount.creator_id.is_(None),
            XAccount.root_review_status != "rejected",
            RosterCentrality.roster_followers >= min_roster_followers,
            # 没有简介就没有任何内容依据，卡片上除了粉丝数什么也写不出来。
            # 与其塞进池子让人逐个点开，不如留给资料补采任务。
            XAccount.bio.is_not(None),
            XAccount.bio != "",
        )
        .order_by(RosterCentrality.roster_followers.desc())
    ).all()
    return [(account, centrality, centrality.roster_followers) for account, centrality in rows]


# =============================================================================
# 池子组装
# =============================================================================


def build_views(
    session: Session,
    *,
    target_accounts: dict[str, XAccount] | None = None,
    min_targets: int = 1,
    include_creators: bool = True,
    include_buyable: bool = True,
    #: roster centrality。默认**关闭**：它排出来的是同圈互推账号，回答的是
    #: 「我们签的人在看谁」，不是「谁能买」。
    include_observed: bool = False,
    include_ingested: bool = True,
    min_roster_followers: int = MIN_ROSTER_FOLLOWERS,
) -> list[CandidateView]:
    """现算出这一轮的可 BD 候选池。

    池子由**能不能买**定义，不由**谁关注了他**定义 —— 见模块 docstring 里的
    审计结论。共同关注在最后作为标记打上去。

    同一个账号常常同时来自几条来源 —— 老板的 sample 里有他、他还是我们库里一条
    没报价的记录。按自然键去重，**保留最完整的那份**（creator > observed），把
    其余来源的依据挂上去，而不是新建一条。这就是「同一账号复用身份记录」在代码
    里的样子。
    """
    # 外部名单永远要读，即使不作为独立来源：它要挂到已有记录上，而不是另起
    # 一条。``include_ingested`` 只决定「库里没有对应记录的」要不要单独列出。
    candidates_by_key: dict[str, BDCandidate] = {}
    candidates_by_creator: dict[int, BDCandidate] = {}
    for candidate in session.scalars(
        select(BDCandidate).options(selectinload(BDCandidate.signals))
    ):
        candidates_by_key[f"{candidate.platform}:{candidate.handle.lower()}"] = candidate
        if candidate.creator_id:
            candidates_by_creator[candidate.creator_id] = candidate

    views: dict[str, CandidateView] = {}

    # --- 来源 1：已在库、还没有报价的对象 -----------------------------------
    if include_creators:
        for creator in creators_without_quote(session):
            view = _creator_view(creator, candidates_by_creator.get(creator.id))
            views[view.key] = view

    # --- 来源 2：观察层里有可买线索的账号 -----------------------------------
    if include_buyable:
        for account, signals in buyable_accounts(session):
            key = f"X:{(account.handle or account.rest_id).lower()}"
            if key in views:
                continue
            view = _observed_view(account, None, 0, 0, candidates_by_key.get(key))
            view.inline_signals = signals
            view.discovery_basis = {
                "source": "buyable_signal",
                "detail": "公开简介里字面写着商务入口",
                "evidence": next(
                    (s.evidence_quote for s in signals if s.evidence_quote), None
                ),
                "caveat": "这只是「他挂了个商务联系方式」，不等于他愿意承接第三方投放",
            }
            views[key] = view

    # --- 来源 3：roster centrality（补充面，默认关闭）-----------------------
    if include_observed:
        roster_size = root_review.roster_account_count(session)
        for account, centrality, followed in observed_accounts(
            session, min_roster_followers=min_roster_followers
        ):
            key = f"X:{(account.handle or account.rest_id).lower()}"
            if key in views:
                continue
            views[key] = _observed_view(
                account, centrality, followed, roster_size, candidates_by_key.get(key),
            )

    # --- 来源 4：外部名单里库中没有对应记录的 -------------------------------
    if include_ingested:
        for key, candidate in candidates_by_key.items():
            if key in views:
                continue
            if candidate.creator is not None:
                view = _creator_view(candidate.creator, candidate)
                views[view.key] = view
            else:
                views[key] = _ingested_view(candidate)

    # --- 最后：把共同关注作为标记打在池子成员身上 ---------------------------
    target_ids = [a.id for a in (target_accounts or {}).values()]
    if target_ids:
        _tag_target_follows(session, views, target_ids, min_targets)

    return list(views.values())


def _tag_target_follows(
    session: Session,
    views: dict[str, CandidateView],
    target_ids: list[int],
    min_targets: int,
) -> None:
    """给池子里的人打上「哪些目标人物关注了他」。

    **只打标记，不增删池子成员。** 一个被 40 位名人关注但买不到的人，不会因为
    这条边进来；一个能买、但一位目标都没关注的人，也不会因此被删掉 —— 后者是
    「优先补充商务路径 / 先确认意愿」里正常的一员。

    ``min_targets`` 在这里只决定标记显不显示，不决定去留。
    """
    # 池子里能对上 X 账号的成员。creator 走 platform_uid / handle，观察层账号
    # 直接就是 XAccount。
    by_account_id: dict[int, CandidateView] = {}
    handles = {
        v.handle.lower(): v for v in views.values() if v.platform == "X" and v.handle
    }
    for view in views.values():
        if view.x_account is not None:
            by_account_id[view.x_account.id] = view
    if handles:
        for chunk in _in_chunks(list(handles)):
            for account in session.scalars(
                select(XAccount).where(func.lower(XAccount.handle).in_(chunk))
            ):
                view = handles.get((account.handle or "").lower())
                if view is not None and account.id not in by_account_id:
                    by_account_id[account.id] = view
                    view.x_account = view.x_account or account
    if not by_account_id:
        return

    source = XAccount.__table__.alias("src")
    snapshot = FollowSnapshot.__table__.alias("snap")
    edges_by_account: dict[int, list[dict]] = {}
    for chunk in _in_chunks(list(by_account_id)):
        rows = session.execute(
            select(
                FollowEdge.target_id,
                source.c.handle,
                source.c.rest_id,
                FollowEdge.first_seen_at,
                FollowEdge.last_seen_at,
                snapshot.c.coverage_limitation,
            )
            .join(source, source.c.id == FollowEdge.source_id)
            .outerjoin(snapshot, snapshot.c.id == FollowEdge.last_seen_snapshot_id)
            .where(
                FollowEdge.source_id.in_(target_ids),
                FollowEdge.target_id.in_(chunk),
                FollowEdge.disappeared_at.is_(None),
            )
        ).all()
        for account_id, handle, rest_id, first_seen, last_seen, limitation in rows:
            edges_by_account.setdefault(account_id, []).append({
                "target_handle": handle,
                "target_rest_id": rest_id,
                "first_observed_at": first_seen.isoformat() if first_seen else None,
                "last_observed_at": last_seen.isoformat() if last_seen else None,
                "coverage_limitation": limitation,
            })

    for account_id, edges in edges_by_account.items():
        if len(edges) < min_targets:
            continue
        view = by_account_id[account_id]
        view.target_follow_edges = sorted(
            edges, key=lambda e: e["target_handle"] or ""
        )
        names = [e["target_handle"] for e in view.target_follow_edges if e["target_handle"]]
        shown = "、".join(f"@{h}" for h in names[:5])
        more = f" 等 {len(names)} 位" if len(names) > 5 else ""
        view.target_follow_note = (
            f"被所选目标人物 {shown}{more} 关注（关注是门槛，不是关系证据）"
        )


def _ingested_view(candidate: BDCandidate) -> CandidateView:
    """只在外部名单里出现过、库里还没有任何记录的候选。

    除了名字和主页什么都没有 —— 附件里的评分和描述**不能**填进内容字段。
    所以适配轴会是 unknown，优先级会落到「仅作目标或观察 / 补充样例」，
    这正是它应有的位置：一个只有别人一句话背书的账号，还不能进建联队列。
    """
    return CandidateView(
        key=f"{candidate.platform}:{candidate.handle.lower()}",
        platform=candidate.platform,
        handle=candidate.handle,
        display_name=candidate.display_name,
        profile_url=candidate.profile_url,
        source_kind="ingested",
        candidate=candidate,
        object_kind=candidate.object_kind,
        object_kind_basis=candidate.object_kind_basis,
        target_group=candidate.target_group,
        platforms=(candidate.platform,),
        discovery_basis={
            "source": "external_list",
            "detail": f"来自外部名单「{candidate.source_name}」",
            "caveat": "名单附带的评分、分层和描述为待核验输入，未参与任何判断",
        },
    )


def stored_signals_by_key(
    session: Session, views: list[CandidateView],
) -> dict[str, list[CommercialSignal]]:
    """一次取完落库的商业信号，按候选分组。

    挂在 creator 上的和挂在候选行上的都要 —— 一条证据是关于这个人的事实，
    换一份名单不会让他重新变得不接广告。
    """
    creator_ids = {v.creator.id for v in views if v.creator}
    candidate_ids = {v.candidate.id for v in views if v.candidate}
    by_creator: dict[int, list[CommercialSignal]] = {}
    by_candidate: dict[int, list[CommercialSignal]] = {}

    for id_chunk, column, sink in (
        (creator_ids, CommercialSignal.creator_id, by_creator),
        (candidate_ids, CommercialSignal.bd_candidate_id, by_candidate),
    ):
        for chunk in _in_chunks(list(id_chunk)):
            for signal in session.scalars(
                select(CommercialSignal).where(column.in_(chunk))
            ):
                key = signal.creator_id if sink is by_creator else signal.bd_candidate_id
                sink.setdefault(key, []).append(signal)

    out: dict[str, list[CommercialSignal]] = {}
    for view in views:
        rows: list[CommercialSignal] = []
        if view.creator:
            rows += by_creator.get(view.creator.id, [])
        if view.candidate:
            rows += by_candidate.get(view.candidate.id, [])
        out[view.key] = rows
    return out


def collection_gaps(
    session: Session,
    prefs: DomainPreferences,
    target_accounts: dict,
    *,
    min_targets: int = 2,
) -> list[dict]:
    """要把候选池扩到 roster 之外，还缺哪些采集 —— 报出来，不要偷偷绕过。

    这是本轮最大的一条限制：我们采过 roster 的关注列表，也采过一批 Root
    候选**与 roster 的交集**，但没有存过任何目标人物的完整关注列表。所以
    「这些目标人物还共同关注了哪些我们库外的账号」目前算不出来 —— 这是
    未采集，不是没有关系。
    """
    gaps: list[dict] = []
    unresolved = [
        handle for handle in prefs.target_handles
        if not any(
            (account.handle or "").lower() == handle.lower() or account.rest_id == handle
            for account in target_accounts.values()
        )
    ]
    if unresolved:
        gaps.append({
            "kind": "target_not_collected",
            "detail": f"{len(unresolved)} 位所选目标人物尚未收录到 X 账号库",
            "items": unresolved,
            "next_step": "采集这些账号的公开资料与关注列表",
        })

    # 同一个判定标准，同一个函数 —— 覆盖率不能在两处各算一遍。
    collected = collected_target_nodes(session, set(target_accounts))
    missing = [
        account.handle or rest_id
        for rest_id, account in target_accounts.items()
        if rest_id not in collected
    ]
    if missing:
        gaps.append({
            "kind": "following_list_not_collected",
            "detail": f"{len(missing)} 位已收录目标人物的公开关注列表尚未采集",
            "items": missing,
            "next_step": "对这些目标人物运行 collect_attention_signals",
        })

    # 被名人共同关注、但公开资料还没采到的账号。这是当前最大的一块缺口：
    # 只有 rest_id 的账号判不出内容，也读不出商业信号，所以它们被挡在队列外 ——
    # 但**它们没有被丢掉**，共同关注人数最高的几个就在这里点名。
    target_ids = [a.id for a in target_accounts.values()]
    if target_ids:
        rows = target_followed_accounts(session, target_ids, min_targets=min_targets)
        pending = [(a, len(edges)) for a, edges in rows if not a.bio and a.creator_id is None]
        if pending:
            pending.sort(key=lambda item: -item[1])
            gaps.append({
                "kind": "cofollowed_profile_not_collected",
                "detail": (
                    f"{len(pending)} 个被 ≥{min_targets} 位目标人物共同关注的账号"
                    f"尚未采到公开资料，暂时无法判断内容与商业信号"
                ),
                "items": [
                    f"@{a.handle or a.rest_id}（{count} 位目标关注）"
                    for a, count in pending[:20]
                ],
                "next_step": (
                    "运行 enrich_cofollowed_profiles --min-targets "
                    f"{min_targets} 补采公开资料"
                ),
            })

    # 被页数截断的关注列表：里面「没有某条边」什么都不能说明。
    if target_accounts:
        truncated = list(session.scalars(
            select(XAccount.handle)
            .join(FollowSnapshot, FollowSnapshot.observer_id == XAccount.id)
            .where(
                XAccount.rest_id.in_(list(target_accounts)),
                FollowSnapshot.coverage_limitation.is_not(None),
            )
            .distinct()
        ).all())
        if truncated:
            gaps.append({
                "kind": "following_list_truncated",
                "detail": (
                    f"{len(truncated)} 位目标人物的关注列表因页数上限被截断，"
                    "其中「没有某条关注边」不能读成「没有关注」"
                ),
                "items": [f"@{h}" for h in truncated if h],
                "next_step": "对这些目标提高 collect_target_following --max-pages 后重采",
            })

    # 目前只有关注边。回复、引用、转发是另一个量级的证据，还没采。
    gaps.append({
        "kind": "interaction_evidence_not_collected",
        "detail": (
            "当前只采集了关注边。目标人物是否**回复、引用、转发过**这些候选尚未采集，"
            "所以每条关系目前都只是研究线索，不能读成互动关系"
        ),
        "items": [],
        "next_step": "对进入建联队列的候选运行 collect_interaction_evidence",
    })
    return gaps
