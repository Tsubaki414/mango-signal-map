"""Signal Map v3 客户接口 —— ``design_handoff_v3/README.md`` 第二节的 9 个接口。

一条链接发给项目方：表达方向 → 看到会进入哪几类人的视野 → 拿到可投放名单 →
保留 → 交回 Mango 复核建联。

可见性
------
这些接口**没有 per-client token**：v3 的默认路径是陌生访客直接开始选，Brief 码
只是把已有项目资料预填进来。所以这一层比 ``/api/client/*`` 暴露面更大，序列化
必须更严 —— 全部**逐字段白名单**，新增的库列在有人把它写进 ``_candidate()``
之前对客户不可见。黑名单是一种「记得住未来每一列」的承诺，而那个承诺一定会破。

绝不下发：联系方式、供应商底价与原始金额、Mango 内部人员与介绍人、内部触达
等级、谈判记录。价格**只以档位** ``$ / $$ / $$$ / $$$$`` 呈现 —— 档位阈值
（``tiers[].lo/hi``）同样不下发，泄露区间等于泄露成本。

未核验就是未核验
----------------
目标人物 0/282,877 经过人工复核（``x_accounts.root_review_status`` 全是
``pending``），关注边 ``human_confirmed`` 全为 0。因此每条边如实带
``verified: false``，纯关注边强度 ``weak``。客户端可以显示，不得表述为已核实。
"""

from __future__ import annotations

import datetime as dt
import json
import os
import secrets
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import v3_catalog as catalog
from . import v3_scoring as scoring
from .bd_models import BDCandidate, CommercialSignal
from .db import session_dependency
from .models import Brief, Creator, Quote, SocialAccount, V3Session
from .observation_models import XAccount

router = APIRouter(prefix="/api", tags=["v3"])

TAXONOMY_PATH = catalog.CONFIG_PATH.parent / "signal_map_taxonomy.json"

#: 演示码 ``demo`` 会打开库里第一条 Brief。设计包要求它存在（首页占位符就写着
#: 「演示输入 demo」），但它绕过了访问码，所以上线前必须关掉。
ALLOW_DEMO_BRIEF = os.getenv("SIGNAL_MAP_ALLOW_DEMO_BRIEF", "1") != "0"

#: 档位阈值只在服务端用来把成本映射成 $ 记号，**永不出现在响应里**。
_TIERS = [(1, 500), (2, 2000), (3, 8000)]

#: 前端词表 → 库内词表。
#:
#: 两套词表是**不重叠**的：``/api/taxonomy`` 来自设计包（us / cn / eu / …、
#: ai_agent / foundation_model / …），而库里存的是 ``normalize.py`` 的
#: ``MARKET_REGIONS`` / ``VERTICALS``（europe_america、consumer_tech…）。
#: 不翻译就等于拿两个不相交的集合做交集 —— 任何市场或方向筛选都恒等于 0，
#: 而且是"安静地"返回 0：接口 200、结构正确、名单空着。
#:
#: 人群词表只是**部分**重叠（founders / investors / developers / enterprise /
#: consumers 两边同名），所以那几个当初碰巧能用，掩盖了另外两个的失效。
_MARKET_MAP = {
    "us": "europe_america",
    "eu": "europe_america",
    "cn": "greater_china",
    "hk": "greater_china",
    "global": "global",
    # 跨境是一种能力，不是一个地区：中英跨境的人同时落在两个大区里。
    "cross": ("greater_china", "europe_america"),
}

_DOMAIN_MAP = {
    "ai_agent": "ai",
    "foundation_model": "ai",
    "enterprise_ai": "ai",
    "consumer_ai": ("ai", "consumer_tech"),
    "creative_ai": ("ai", "design"),
    "dev_tools": "developer_tools",
    "ai_coding": ("ai", "developer_tools"),
    "crypto": "crypto",
    "defi": "crypto",
    "web3_infra": "crypto",
    "fintech": "finance",
    "trading": "finance",
    "us_stocks": "finance",
}


def _contract(values: list[str], mapping: dict[str, object]) -> list[str]:
    """库内词表 → 前端词表，``_expand`` 的反向。

    预填必须还原成前端认得的词。Brief 里存的是 ``ai``，而 taxonomy 里只有
    ``ai_agent`` / ``foundation_model`` —— 直接把 ``ai`` 回给前端，界面上一个
    选项都勾不中，客户看到的就是「带入了 Brief，但什么都没填」。

    一个库内词可能对应多个前端词（``ai`` → 四个 AI 细分），全部返回：让客户
    取消掉不要的，好过替他猜一个。
    """
    out: list[str] = []
    for v in values:
        hits = [
            front
            for front, back in mapping.items()
            if v == back or (isinstance(back, tuple) and v in back)
        ]
        out.extend(hits or [v])
    return list(dict.fromkeys(out))


def _expand(values: list[str], mapping: dict[str, object]) -> set[str]:
    """把前端词表展开成库内词表。映射不到的原样保留 —— 两边同名的（人群）
    本来就不需要翻译，硬要求映射反而会把它们丢掉。"""
    out: set[str] = set()
    for v in values:
        mapped = mapping.get(v, v)
        if isinstance(mapped, tuple):
            out.update(mapped)
        else:
            out.add(str(mapped))
    return out


#: discovered 相对 priced 的数量上限。
#:
#: 设计包写的是 priced:discovered ≈ 2:1（发现的人是已报价的一半）。实际跑出来
#: 是 1:7.2 —— 因为被目标人物关注的已报价创作者只有 22 位，而发现池有 158 位。
#:
#: 项目方定为 **1:5**（发现的人最多是已报价的 5 倍），这是一个有意偏离规格的
#: 产品决定：发现从未建联过的人是这个产品最值钱的部分，压到 1:2 等于把它砍掉
#: 大半。超出部分按连接强度截断，留下的是连接最强的那些。
DISCOVERED_RATIO = 5

#: 无论比例算出来多少，发现候选不少于这个数（池子够大时）。
#:
#: 比例规则的分母是「被目标人物关注的已报价创作者数」，而这个数在小领域里
#: 极小：金融只有 1 位，乘 5 只剩 5 位，把原本 67 位的发现池截掉 62 位。
#: 那不是「发现得太多」，是**供给覆盖太薄**，不该由比例规则来惩罚客户 ——
#: 恰恰是这种领域最需要「我们再去帮你找人」。
#:
#: 所以比例只在分母够大时才起作用；分母小的时候由这条下限兜住。
DISCOVERED_FLOOR = 30

#: 内部取发现池的上限。对外的 ``limit`` 只决定**返回多少条**，两者必须分开：
#: 池子小了会漏掉本该排进前列的人，池子大了只是多算一点分。
POOL_LIMIT = 400

#: 执行队列 A/B/C/D。
#:
#: ``AUDIT-v3`` 第 4 条：「A/B/C/D 不是最终推荐，而是**执行队列**」。原来只有
#: 「可赞助已确认 / 商务待验证」两态，缺了 C 和 D —— 而这两类的**处置方式
#: 完全不同**：媒体按库存采购而不是按个人声量，观察类根本不该推进。
#:
#: 队列不是评分，不是"谁更好"。它回答的是「拿到这份名单之后，对这个人具体
#: 做什么」——所以每一档都带 ``action``，没有动作的队列只是个漂亮标签。
EXEC_QUEUES = {
    "A": {
        "label": "可优先商务确认",
        "action": "问报价、样张、档期、可定制角度",
    },
    "B": {
        "label": "需 BD 验证",
        "action": "确认联系方式、接单意愿、历史合作与报价区间",
    },
    "C": {
        "label": "Warm intro / 媒体路径",
        "action": "走媒体库存洽谈，不按普通 paid KOL 处理",
    },
    "D": {
        "label": "内部观察",
        "action": "暂不推进，留作观察",
    },
}


def _queue_of(*, biz_state: str, object_kind: str | None) -> str:
    """把已有的商务状态与身份判定映射到执行队列。

    顺序有讲究：**媒体优先于商务状态**。一个媒体号即使已有报价，采购方式仍然
    是买库存位而不是买个人声量，走 A 的流程（问样张、问可定制角度）是错的。
    """
    if object_kind == "media_channel":
        return "C"
    if biz_state == "ready":
        return "A"
    if biz_state in ("open_channel", "needs_bd"):
        return "B"
    return "D"


#: v3 商务三态的客户端文案。三态是 README 第三节定义的闭集。
BIZ_STATE_LABELS = {
    "ready": "可立即确认报价与档期",
    "open_channel": "有公开合作入口，待接洽",
    "needs_bd": "需 Mango 主动建联",
}


def new_session_id() -> str:
    """不可猜的会话 id。12 字符 base64url ≈ 72 bit 熵，足够当链接里的凭证。"""
    return secrets.token_urlsafe(9)


def _labels() -> dict[str, dict[str, str]]:
    """id -> 中文标签。理由和分项里出现的是给客户看的词，不是库里的 key。"""
    tax = _taxonomy()
    out = {k: {x["id"]: x.get("label", x["id"]) for x in tax.get(k, [])}
           for k in ("domains", "audiences", "markets", "languages", "formats")}
    out["circles"] = {c.id: c.label for g in catalog.load_config() for c in g.circles}
    # 库内词表也要能翻译：发现候选的画像用的是 normalize.py 的词
    # （ai、consumer_tech、europe_america），它们不在前端 taxonomy 里。
    out["domains"].update({
        "ai": "AI", "developer_tools": "开发者工具", "crypto": "Crypto",
        "finance": "金融", "robotics": "机器人", "marketing": "营销",
        "business": "商业", "design": "设计", "media_video": "影音",
        "education": "教育", "consumer_tech": "消费科技", "gaming": "游戏",
        "science": "科研",
    })
    out["markets"].update({
        "europe_america": "欧美", "greater_china": "中文圈", "latam": "拉美",
        "japan_korea": "日韩", "sea": "东南亚", "south_asia": "南亚",
        "mena": "中东", "africa": "非洲", "global": "全球",
    })
    return out


def _taxonomy() -> dict[str, Any]:
    raw = json.loads(TAXONOMY_PATH.read_text(encoding="utf-8"))
    raw.pop("_readme", None)
    # 档位只下发 mark 与 label。lo/hi 是成本区间，留在服务端。
    raw["tiers"] = [
        {"id": t["id"], "mark": t["mark"], "label": t["label"]} for t in raw.get("tiers", [])
    ]
    return raw


def tier_of(cost_usd: float | None) -> int | None:
    """内部成本 → 档位。返回档位序号，调用方只能把它转成 ``$`` 记号。"""
    if cost_usd is None:
        return None
    for tier, ceiling in _TIERS:
        if cost_usd < ceiling:
            return tier
    return 4


def _csv(value: str | None) -> list[str]:
    return [x.strip() for x in (value or "").split(",") if x.strip()]


def _edges(member: catalog.PoolMember) -> list[dict[str, Any]]:
    return [
        {
            "targetId": e.target_id,
            "type": e.type,
            "count": e.count,
            "lastSeen": e.last_seen,
            "verified": e.verified,
            "strength": e.strength,
            # evidence 是原始观察记录，可能含采集备注，只在确有其值时下发。
            **({"evidence": e.evidence} if e.evidence else {}),
        }
        for e in member.edges
    ]


# --------------------------------------------------------------------------
# 只读目录
# --------------------------------------------------------------------------


@router.get("/meta")
def meta(session: Session = Depends(session_dependency)) -> dict[str, Any]:
    """首页数字。``kols`` 只数**有真实报价**的创作者 —— 没有报价无法进预算。"""
    priced = session.scalars(
        select(Quote.creator_id).where(Quote.internal_cost_usd.is_not(None))
    ).all()
    platforms = session.scalars(
        select(SocialAccount.platform).where(SocialAccount.platform.is_not(None)).distinct()
    ).all()
    tax = _taxonomy()
    return {
        "kols": len(set(priced)),
        "platforms": len({p for p in platforms if p}),
        "groups": len(catalog.load_config()),
        "markets": len(tax.get("markets", [])),
    }


@router.get("/taxonomy")
def taxonomy() -> dict[str, Any]:
    return _taxonomy()


@router.get("/groups")
def groups(session: Session = Depends(session_dependency)) -> list[dict[str, Any]]:
    """领域与圈层。

    每个圈层附 ``coverage``：该圈层有几位目标人物、其中几位**已采集到关注边**。
    采集为 0 的圈层前端要显示「当前未观察到」，而不是一个空列表 —— 空列表会被
    读成「这个圈层没人」，那是两回事。
    """
    out = []
    for g in catalog.load_config():
        resolved = catalog.resolve_targets(session, g.id)
        by_circle: dict[str, list[catalog.ResolvedTarget]] = {}
        for t in resolved:
            by_circle.setdefault(t.definition.circle, []).append(t)
        out.append(
            {
                "id": g.id,
                "label": g.label,
                "desc": g.desc,
                "domains": list(g.domains),
                "circles": [
                    {
                        "id": c.id,
                        "label": c.label,
                        "value": c.value,
                        "who": c.who,
                        "pick": c.pick,
                        "coverage": {
                            "targets": len(by_circle.get(c.id, [])),
                            "collected": sum(
                                1 for t in by_circle.get(c.id, []) if t.collected
                            ),
                        },
                    }
                    for c in g.circles
                ],
            }
        )
    return out


@router.get("/targets")
def targets(
    group: str = Query(..., description="领域 id"),
    session: Session = Depends(session_dependency),
) -> list[dict[str, Any]]:
    """圈层代表人物。**仅展示，不可被客户勾选** —— 勾选会让名单越选越少。"""
    if catalog.group_by_id(group) is None:
        raise HTTPException(status_code=404, detail="未找到该领域")
    out = []
    for t in catalog.resolve_targets(session, group):
        d = t.definition
        out.append(
            {
                "id": d.id,
                "name": d.name,
                "handle": f"@{d.handle}",
                "role": d.role,
                "group": d.group,
                "circle": d.circle,
                "why": d.why,
                "markets": list(d.markets),
                "audiences": list(d.audiences),
                # 和候选卡用同一个来源。一度写死成 None，结果圈层里每个人都是
                # 首字母方块 —— 而目标人物是「你想影响的人」，一张脸和一个字母
                # 给客户的分量完全不同。
                "avatarUrl": f"https://unavatar.io/x/{d.handle}" if d.handle else None,
                # 采集状态如实下发，前端据此显示「关系数据待补充」。
                "collected": t.collected,
                # 复核状态。当前全库 pending —— 没有任何目标人物经人工确认。
                "reviewed": t.review_status == "accepted",
            }
        )
    return out


def _candidate(
    member: catalog.PoolMember,
    *,
    creator: Creator | None,
    account: XAccount | None,
    social: SocialAccount | None = None,
    quote: Quote | None,
    has_commercial_signal: bool,
    group: str,
    object_kind: str | None = None,
) -> dict[str, Any]:
    """把池子成员摊平成 v3 的 ``Candidate``。**逐字段白名单。**"""
    if member.source == "priced":
        name = creator.display_name if creator else None
        # handle/followers 只能从 SocialAccount 取：``Creator.primary_handle``
        # 在这份数据里普遍是 NULL，而 Creator 没有 social_accounts 关系，
        # 读它只会静默得到 None，卡片上就是一个没有账号也没有粉丝数的人。
        handle = (social.handle if social else None) or (
            creator.primary_handle if creator else None
        )
        followers = social.followers if social else None
    else:
        name = (account.display_name if account else None) or member.handle
        handle = member.handle
        followers = account.followers if account else None

    if quote is not None and not quote.needs_review:
        biz_state = "ready"
        biz_evidence = "已有报价记录，可确认档期"
    elif has_commercial_signal:
        biz_state = "open_channel"
        biz_evidence = "公开资料中有合作入口"
    else:
        biz_state = "needs_bd"
        biz_evidence = "尚未建联，由 Mango 出面接洽"

    clean_handle = (handle or "").lstrip("@")
    payload: dict[str, Any] = {
        "id": f"c{member.creator_id}" if member.creator_id else f"x{member.account_id}",
        "name": name,
        "handle": f"@{clean_handle}" if clean_handle else None,
        # 头像走 unavatar，前端有首字母兜底。生产环境应自托管：这个第三方
        # 代理会知道 Mango 在研究哪些账号（v3 README 第十节第 3 条）。
        "avatarUrl": f"https://unavatar.io/x/{clean_handle}" if clean_handle else None,
        "url": f"https://x.com/{clean_handle}" if clean_handle else None,
        "platform": "X",
        "followers": followers,
        "group": group,
        "source": member.source,
        "bizState": biz_state,
        "bizEvidence": biz_evidence,
        "bizLabel": BIZ_STATE_LABELS[biz_state],
        "queue": (q := _queue_of(biz_state=biz_state, object_kind=object_kind)),
        "queueLabel": EXEC_QUEUES[q]["label"],
        "queueAction": EXEC_QUEUES[q]["action"],
        "edges": _edges(member),
        "risk": [],
    }

    if member.source == "priced" and creator is not None:
        payload |= {
            "type": creator.creator_class,
            # 只有档位，没有金额。internal_cost_usd 永不出现在这一层，
            # 也永不出现在任何生成的句子里 —— 文案会绕过字段白名单。
            "tier": tier_of(quote.internal_cost_usd) if quote else None,
            "quoteStatus": "confirmed" if quote and not quote.needs_review else "historical_unverified",
            "markets": _csv(creator.market_region),
            "languages": _csv(creator.languages),
            "domains": _csv(creator.verticals),
            "audiences": _csv(creator.audience_types),
        }
    else:
        # 发现候选的画像从公开简介规则化推导（见 catalog.profile_from_bio）。
        # 不推导的话，市场/方向筛选对这一半完全不起作用 —— 客户改了条件，
        # 名单里一百多个人纹丝不动，那不是"数据缺失"，那是筛选坏了。
        profile = catalog.profile_from_bio(account.bio if account else None)
        payload |= {
            "type": None,
            "bio": (account.bio if account else None),
            "domains": list(profile.domains),
            "markets": [profile.market] if profile.market else [],
            "languages": [profile.language] if profile.language else [],
            # 人群不猜。用正则从简介推受众被验证过是错的（它读的是「这个人做
            # 什么」，不是「谁在看」），所以这里恒为空，客户端显示「人群待确认」。
            "audiences": [],
            # 依据要能显示出来：卡片上写「依据：简介语言」，而不是假装这是实测。
            "profileBasis": {
                "domains": profile.domains_basis,
                "market": profile.market_basis,
            },
            "profileStatus": "derived" if (profile.domains or profile.market) else "unknown",
            # 经由哪几位目标人物找到 —— discovered 的可解释性全靠这一条。
            "discoveryPath": sorted({e.target_id for e in member.edges}),
        }
    return payload



def _target_handle(resolved: list, target_id: str) -> str | None:
    for t in resolved:
        if t.definition.id == target_id:
            return t.definition.handle
    return None


def _sort_key(item: dict[str, Any], circle_of: dict[str, str], focus: set[str]) -> float:
    """排序键：重点圈层里的连接权重。没标重点时退化为全部连接权重。

    focus 只改顺序，**从不过滤** —— 早期版本让客户勾目标人物，每勾一个名单就
    收窄（26 → 4），客户越参与结果越少。
    """
    total = 0.0
    for e in item.get("edges", []):
        w = scoring.EDGE_WEIGHTS.get(e.get("type", "cofollow"), 1.0)
        if not e.get("verified"):
            w *= scoring.UNVERIFIED_FACTOR
        if focus and circle_of.get(e.get("targetId", "")) not in focus:
            continue
        total += w
    return total


@router.get("/candidates")
def candidates(
    group: str = Query(...),
    markets: str | None = Query(None),
    domains: str | None = Query(None),
    audiences: str | None = Query(None),
    focus: str | None = Query(None, description="重点圈层 id，逗号分隔，最多 3 个"),
    limit: int = Query(120, le=400, description="返回多少条（排序后截断），不是发现池大小"),
    session: Session = Depends(session_dependency),
) -> dict[str, Any]:
    """候选池 —— **priced 与 discovered 两类都返回**。

    刻意**不接受** ``targetIds`` 过滤。早期版本让客户勾目标人物，每勾一个名单就
    收窄（26 → 4），客户越参与结果越少。重点圈层只改排序，数量永不下降。
    """
    if catalog.group_by_id(group) is None:
        raise HTTPException(status_code=404, detail="未找到该领域")

    resolved = catalog.resolve_targets(session, group)
    priced = catalog.priced_pool(session, resolved)
    # 取池子用固定上限，**不跟着对外的 limit 走**。
    #
    # 这两件事一度是同一个数：limit=5 时发现池只取 5 个，而已报价那 22 位
    # 无论如何全量返回 —— 于是 limit=5 拿回 27 条，limit=60 拿回 82 条，
    # 调用方以为自己在控制页大小，其实只在控制发现池，而且截断发生在排序
    # 之前，拿到的根本不是前 N 名。
    discovered = catalog.discovered_pool(session, resolved, limit=POOL_LIMIT)

    creators = catalog.creators_by_id(session, [m.creator_id for m in priced if m.creator_id])
    quotes: dict[int, Quote] = {}
    for q in session.scalars(
        select(Quote).where(
            Quote.creator_id.in_(list(creators)), Quote.internal_cost_usd.is_not(None)
        )
    ).all():
        kept = quotes.get(q.creator_id)
        if kept is None or (q.internal_cost_usd or 0) > (kept.internal_cost_usd or 0):
            quotes[q.creator_id] = q

    socials: dict[int, SocialAccount] = {}
    for sa in session.scalars(
        select(SocialAccount).where(SocialAccount.creator_id.in_(list(creators)))
    ).all():
        kept_sa = socials.get(sa.creator_id)
        if kept_sa is None or (sa.followers or 0) > (kept_sa.followers or 0):
            socials[sa.creator_id] = sa

    accounts = {
        a.id: a
        for a in session.scalars(
            select(XAccount).where(
                XAccount.id.in_([m.account_id for m in discovered if m.account_id])
            )
        ).all()
    }
    # handle -> 身份判定。人工判定压过机器建议。
    kind_by_handle = {
        (h or "").lower(): (k if k and k != "unknown" else sg)
        for h, k, sg in session.execute(
            select(BDCandidate.handle, BDCandidate.object_kind, BDCandidate.object_kind_suggested)
            .where(BDCandidate.handle.is_not(None))
        ).all()
    }

    signalled = set(
        session.scalars(select(CommercialSignal.bd_candidate_id).distinct()).all()
    )
    handles_with_signal = {
        (h or "").lower()
        for (h,) in session.execute(
            select(BDCandidate.handle).where(BDCandidate.id.in_(signalled))
        ).all()
    }

    items = [
        _candidate(
            m,
            creator=creators.get(m.creator_id),
            account=None,
            social=socials.get(m.creator_id),
            quote=quotes.get(m.creator_id),
            has_commercial_signal=True,
            group=group,
            object_kind=kind_by_handle.get(
                (socials.get(m.creator_id).handle or "").lower()
                if socials.get(m.creator_id) else ""
            ),
        )
        for m in priced
    ] + [
        _candidate(
            m,
            creator=None,
            account=accounts.get(m.account_id),
            quote=None,
            has_commercial_signal=(m.handle or "").lower() in handles_with_signal,
            group=group,
            object_kind=kind_by_handle.get((m.handle or "").lower()),
        )
        for m in discovered
    ]

    wanted_markets = _expand(_csv(markets), _MARKET_MAP)
    wanted_domains = _expand(_csv(domains), _DOMAIN_MAP)
    wanted_audiences = _expand(_csv(audiences), {})

    def matches(item: dict[str, Any]) -> bool:
        """两侧都筛，但**只按已知值筛**。

        有值且对不上 → 淘汰；没有值 → 保留并标 ``profileStatus: unknown``，
        由客户端显示「待确认」。这既让筛选真的生效，又守住「缺数据降低置信度、
        从不删候选」—— 两者并不冲突，冲突的是"整批放行"和"按缺失淘汰"这两个
        极端。
        """
        for wanted, key in (
            (wanted_markets, "markets"),
            (wanted_domains, "domains"),
            (wanted_audiences, "audiences"),
        ):
            if not wanted:
                continue
            have = set(item.get(key) or [])
            if have and not wanted & have:
                return False
        return True

    active = [
        (wanted_markets, "markets"),
        (wanted_domains, "domains"),
        (wanted_audiences, "audiences"),
    ]

    def relevance_of(item: dict[str, Any]) -> str:
        """匹配 / 相关性待确认。

        「缺数据不淘汰」不等于「缺数据可以混进匹配结果」。筛 dev_tools 返回
        33 位、其中 28 位方向未知时，把它们排在一起等于告诉客户「这 33 个都
        合适」—— 实际是 5 个合适、28 个我们不知道。
        """
        for wanted, key in active:
            if wanted and not (item.get(key) or []):
                return "unknown"
        # 没有任何筛选条件时，方向本身空着也算相关性未确认 —— MrBeast 那类
        # 泛账号靠名人关注排进前列，正是因为这一项一直被当成中性。
        if not item.get("domains"):
            return "unknown"
        return "matched"

    def kept_only_because_unknown(item: dict[str, Any]) -> bool:
        """这个人留下来，是因为**被筛的那个维度**恰好判不出来。

        必须按维度算，不能用一个笼统的 profileStatus：市场几乎总能从简介文字
        判出来，所以哪怕领域完全未知，整体状态也会显示"已推导"。客户筛了方向、
        名单里却留着一批方向未知的人，而界面上一个字都没说 —— 那是在用沉默
        冒充匹配。
        """
        return any(wanted and not (item.get(key) or []) for wanted, key in active)

    kept = [i for i in items if matches(i)]

    # —— 按比例截断 discovered ——
    #
    # 在评分之前截，因为截掉的那些根本不需要算分。保留连接最强的（pool 已按
    # 共同关注数排序），截断数量单独报出来，不假装池子本来就这么大。
    priced_kept = [i for i in kept if i["source"] == "priced"]
    disc_kept = [i for i in kept if i["source"] == "discovered"]
    cap = max(DISCOVERED_FLOOR, len(priced_kept) * DISCOVERED_RATIO)
    trimmed = max(0, len(disc_kept) - cap)
    if trimmed:
        disc_kept = disc_kept[:cap]
    kept = priced_kept + disc_kept

    # —— 评分、理由、排序 ——
    #
    # 排序主依据是**重点圈层的连接权重**（focus_score），不是综合分 fit。
    # 把维度揉成一个分数再排，会稀释「这个人能把内容送进你要影响的人的视野」
    # 这个唯一清晰的信号。fit 只作为可展开的解释存在，且永远和五条分项一起返回。
    circle_of_target = {t.definition.id: t.definition.circle for t in resolved}
    collected_per_circle: dict[str, int] = {}
    for t in resolved:
        if t.collected:
            collected_per_circle[t.definition.circle] = (
                collected_per_circle.get(t.definition.circle, 0) + 1
            )
    target_names = {t.definition.id: t.definition.name for t in resolved}
    labels = _labels()
    focus_set = set(_csv(focus)[:3])

    # 配置外的已观察账号：它关注了这个候选。是**证据**，不是画像 ——
    # 不参与圈层归属，也不进路径图。
    observed_extra = catalog.other_signals(
        session,
        [m.creator_id for m in priced if m.creator_id],
        exclude_nodes={t.account.rest_id for t in resolved if t.account and t.account.rest_id},
    )
    by_item_id = {f"c{cid}": sigs for cid, sigs in observed_extra.items()}

    for item in kept:
        sc = scoring.score(
            edges=item["edges"],
            circle_of_target=circle_of_target,
            target_names=target_names,
            domains=item.get("domains") or [],
            audiences=item.get("audiences") or [],
            markets=item.get("markets") or [],
            tier=item.get("tier"),
            prefs={"domains": list(wanted_domains), "audiences": list(wanted_audiences),
                   "markets": list(wanted_markets)},
            focus=focus_set,
            labels=labels,
            collected_per_circle=collected_per_circle,
        )
        item |= {
            "fit": sc.fit,
            "strength": sc.strength,
            "strengthLabel": sc.strength_label,
            "strengthAdvice": sc.strength_advice,
            "band": sc.band_word,
            "bandLevel": sc.band_level,
            "parts": [p.as_dict() for p in sc.parts],
            "reason": sc.reason,
            "overlapText": sc.overlap_text,
            "focusNote": sc.focus_note,
            "circles": sc.circles,
            # 哪几位目标人物连着他 —— 卡片上那排叠加的小头像。
            "faces": [
                {"id": tid, "name": target_names.get(tid, ""),
                 "handle": f"@{h}" if (h := _target_handle(resolved, tid)) else None,
                 "avatarUrl": f"https://unavatar.io/x/{h}" if h else None}
                for tid in dict.fromkeys(e["targetId"] for e in item["edges"])
            ][:5],
            # 附加证据：粉丝量一起给，让人自己判断分量 —— 不做互关池自动判定，
            # 因为 Elon Musk 关注了我方 63% 的创作者，任何占比规则都会误判他。
            "otherSignals": [
                {"handle": f"@{o.handle}", "followers": o.followers,
                 "interactions": o.interactions}
                for o in by_item_id.get(item["id"], [])
            ],
        }

    for item in kept:
        item["relevance"] = relevance_of(item)

    # 已报价的排在最前，两侧各自按连接强度排。
    #
    # 纯按连接强度排的话，发现候选里的名人（被更多目标人物关注）会占满前几屏，
    # 客户第一眼看到的全是「需 Mango 主动建联」—— 观感是这产品一个能立刻推进的
    # 人都没有，而实际上有 22 位随时可以确认报价和档期。
    #
    # 这是有意偏离「排序主依据是连接」：连接强度仍然决定**组内**顺序，
    # 但「现在就能买」是比「连接更强」更前置的一件事。
    kept.sort(
        key=lambda i: (
            0 if i["relevance"] == "matched" else 1,
            0 if i["source"] == "priced" else 1,
            -_sort_key(i, circle_of_target, focus_set),
            -i["fit"],
            -len(i["edges"]),
        )
    )

    # 排序**之后**才截断，这样拿到的才是前 N 名。
    #
    # 但不能直接切前 N：发现候选里的名人被更多目标人物关注，连接强度几乎总是
    # 压过已报价创作者，直接切会让第一页 priced=0 —— 客户翻开名单，一个能立刻
    # 确认的人都没有。所以 1:5 的比例在**返回的这一页里**也要成立，两侧各自
    # 保持连接强度的顺序。
    pool_total = len(kept)
    if pool_total > limit:
        by_priced = [i for i in kept if i["source"] == "priced"]
        by_disc = [i for i in kept if i["source"] == "discovered"]
        want_priced = min(len(by_priced), max(1, round(limit / (DISCOVERED_RATIO + 1))))
        # 已报价的在前，其余按连接强度补满。不再打散重排 —— 分块正是要的效果。
        kept = by_priced[:want_priced] + by_disc[: limit - want_priced]
    return {
        "group": group,
        "counts": {
            # total 是**本次返回的条数**；池子实际有多大看 poolTotal。
            "total": len(kept),
            "poolTotal": pool_total,
            "hasMore": pool_total > len(kept),
            "priced": sum(1 for i in kept if i["source"] == "priced"),
            "discovered": sum(1 for i in kept if i["source"] == "discovered"),
            # 有多少人是"所筛维度判不出来所以留下的"，客户端要如实说明。
            "keptAsUnknown": sum(1 for i in kept if kept_only_because_unknown(i)),
            "matched": sum(1 for i in kept if i["relevance"] == "matched"),
            "relevanceUnknown": sum(1 for i in kept if i["relevance"] == "unknown"),
            # 因比例上限被截掉的发现候选数。池子有多大是事实，不该藏起来。
            "discoveredTrimmed": trimmed,
        },
        "coverage": {
            "targets": len(resolved),
            "collected": sum(1 for t in resolved if t.collected),
            "reviewed": sum(1 for t in resolved if t.review_status == "accepted"),
        },
        "items": kept,
    }


class DiscoverIn(BaseModel):
    group: str
    circles: list[str] | None = None


@router.post("/discover")
def discover(
    payload: DiscoverIn, session: Session = Depends(session_dependency)
) -> dict[str, Any]:
    """重算共同关注子集。

    ``circles`` 只**缩小取边的目标人物集合**，不过滤已有候选 —— 和 focus 一样，
    这个接口永远不会让客户看到的人变少。
    """
    if catalog.group_by_id(payload.group) is None:
        raise HTTPException(status_code=404, detail="未找到该领域")
    resolved = catalog.resolve_targets(session, payload.group)
    if payload.circles:
        wanted = set(payload.circles)
        resolved = [t for t in resolved if t.definition.circle in wanted]
    found = catalog.discovered_pool(session, resolved, limit=200)

    # 账号必须一起取出来。这里一度传 account=None，结果 158 条候选全部
    # followers/bio 为空、name 退化成 handle（「lexfridman」而不是「Lex Fridman」），
    # 画像也无从推导 —— 接口 200、条数对，内容是空壳。
    accounts = {
        a.id: a
        for a in session.scalars(
            select(XAccount).where(XAccount.id.in_([m.account_id for m in found if m.account_id]))
        ).all()
    }
    return {
        "group": payload.group,
        "circles": payload.circles or [],
        "threshold": catalog.cofollow_threshold(sum(1 for t in resolved if t.collected)),
        "found": len(found),
        "items": [
            _candidate(
                m, creator=None, account=accounts.get(m.account_id), quote=None,
                has_commercial_signal=False, group=payload.group,
            )
            for m in found
        ],
    }


@router.get("/brief/{code}")
def brief(code: str, session: Session = Depends(session_dependency)) -> dict[str, Any]:
    """Brief 码 → 预填偏好。无效返回 404。

    码目前就是 ``Brief.id``（``demo`` 取第一条）。真实的签发与校验仍待后端补上
    ——见 v3 README 第十节第 1 条；在那之前这个接口不承担鉴权作用。
    """
    # **绝不按主键查**。一度支持 /api/brief/1，于是从 1 数上去就能读到别的
    # 客户的项目名、方向、市场与预算档 —— 这个接口没有任何鉴权，码就是凭证。
    row = session.scalar(select(Brief).where(Brief.access_code == code))
    if row is None and code == "demo" and ALLOW_DEMO_BRIEF:
        row = session.scalars(select(Brief).order_by(Brief.id)).first()
    if row is None:
        raise HTTPException(status_code=404, detail="未找到该 Brief")
    # 字段名必须和 Brief 的真实列一致。这里一度写成 market_regions /
    # languages / audience_types，配合 getattr(..., None) 静默返回 None ——
    # 带 Brief 码进来的客户，市场/语言/人群预填**永远是空的**，接口还是 200。
    # 读一个可能不存在的列时不要用 getattr 兜底，它会把拼错变成空值。
    return {
        "code": code,
        "name": row.name,
        "prefs": {
            "group": row.domain_group,
            "domains": _contract(_csv(row.verticals), _DOMAIN_MAP),
            "markets": _contract(_csv(row.target_markets), _MARKET_MAP),
            "languages": _csv(row.content_languages),
            "audiences": _csv(row.target_audiences),
            "budget": _budget_band(row.total_budget_usd),
            "brand": [],
        },
    }


def _budget_band(total: float | None) -> str | None:
    """总预算 → taxonomy 里的预算档 id。客户选的是档，不是数字。"""
    if total is None:
        return None
    for band in _taxonomy().get("budgets", []):
        if band["lo"] <= total <= band["hi"]:
            return band["id"]
    return None


# --------------------------------------------------------------------------
# 会话
# --------------------------------------------------------------------------


class SessionIn(BaseModel):
    prefs: dict[str, Any] | None = None
    focus: list[str] = Field(default_factory=list)
    custom: list[dict[str, Any]] = Field(default_factory=list)
    plans: dict[str, Any] | None = None
    plan: str = "A"
    listOpen: bool = False


def _session_out(row: V3Session) -> dict[str, Any]:
    return {
        "id": row.id,
        "prefs": json.loads(row.prefs) if row.prefs else None,
        "focus": json.loads(row.focus) if row.focus else [],
        "custom": json.loads(row.custom) if row.custom else [],
        "plans": json.loads(row.plans) if row.plans else {"A": [], "B": []},
        "plan": row.plan,
        "listOpen": row.list_open,
        "submittedAt": row.submitted_at.isoformat() if row.submitted_at else None,
    }


@router.post("/sessions", status_code=201)
def create_session(
    brief_code: str | None = None, session: Session = Depends(session_dependency)
) -> dict[str, Any]:
    """开一个新会话，**id 由服务端签发**。

    这个接口存在的唯一理由是安全。id 曾经由客户端自己指定、``PUT`` 顺手创建，
    于是 ``/api/sessions/acme-2026`` 这种能猜到的 id 谁都能读、能覆盖 —— 而这
    一层没有 per-client token，链接本身就是凭证。凭证不能让调用方自己起名。
    """
    row = V3Session(id=new_session_id(), brief_code=brief_code)
    session.add(row)
    session.commit()
    return _session_out(row)


@router.put("/sessions/{session_id}")
def put_session(
    session_id: str, payload: SessionIn, session: Session = Depends(session_dependency)
) -> dict[str, Any]:
    row = session.get(V3Session, session_id)
    if row is None:
        # PUT **不创建**。允许创建就等于允许占用任意 id，能猜的 id 就不再是凭证。
        raise HTTPException(status_code=404, detail="未找到该会话")
    if row.submitted_at is not None:
        # 已提交的名单是 Mango 要去履约的东西，静默改写是合同问题。
        raise HTTPException(status_code=409, detail="该名单已提交，如需修改请联系 Mango")

    # focus 最多 3 个：超出时挤掉最早的，并在响应里告知。
    focus = payload.focus[-3:]
    row.prefs = json.dumps(payload.prefs, ensure_ascii=False) if payload.prefs else None
    row.focus = json.dumps(focus, ensure_ascii=False)
    row.custom = json.dumps(payload.custom, ensure_ascii=False)
    row.plans = json.dumps(payload.plans, ensure_ascii=False) if payload.plans else None
    row.plan = payload.plan
    row.list_open = payload.listOpen
    session.commit()

    out = _session_out(row)
    if len(payload.focus) > 3:
        out["notice"] = "最多可标记 3 个重点圈层，已保留最近选择的 3 个。"
    return out


@router.get("/sessions/{session_id}")
def get_session(
    session_id: str, session: Session = Depends(session_dependency)
) -> dict[str, Any]:
    row = session.get(V3Session, session_id)
    if row is None:
        raise HTTPException(status_code=404, detail="未找到该会话")
    return _session_out(row)


@router.post("/sessions/{session_id}/submit")
def submit_session(
    session_id: str, session: Session = Depends(session_dependency)
) -> dict[str, Any]:
    row = session.get(V3Session, session_id)
    if row is None:
        raise HTTPException(status_code=404, detail="未找到该会话")
    if row.submitted_at is None:
        row.submitted_at = dt.datetime.now(dt.UTC).replace(tzinfo=None)
        session.commit()
    plans = json.loads(row.plans) if row.plans else {}
    kept = plans.get(row.plan) or []
    return {
        "id": row.id,
        "submittedAt": row.submitted_at.isoformat(),
        "kept": len(kept),
        # 措辞受约束：确认不是购买，也不承诺任何目标人物一定会看到。
        "receipt": f"已收到 {len(kept)} 位创作者的初步名单。Mango 将复核报价、"
        f"档期与合作条件后回复。确认不是购买。",
    }


class WantedIn(BaseModel):
    name: str
    hint: str | None = None


@router.post("/sessions/{session_id}/wanted")
def add_wanted(
    session_id: str, payload: WantedIn, session: Session = Depends(session_dependency)
) -> dict[str, Any]:
    """客户手填的库外目标人物，交 BD 核查。

    **不写进目标人物库** —— 客户说某人重要，是一条待核查线索，不是已确认的
    Root。写进库会让一个未经核实的名字获得和已采集目标人物同等的地位。
    """
    row = session.get(V3Session, session_id)
    if row is None:
        raise HTTPException(status_code=404, detail="未找到该会话")
    current = json.loads(row.custom) if row.custom else []
    current.append({"name": payload.name, "hint": payload.hint, "status": "pending_bd"})
    row.custom = json.dumps(current, ensure_ascii=False)
    session.commit()
    return {"custom": current, "notice": "已记录，Mango 会核查后回复是否可覆盖。"}
