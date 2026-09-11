"""采集**客户选定的目标人物**关注了谁 —— 反推可投放 KOL 的那一步。

这是整套方法的核心，也是之前缺的一块。Dov 的逻辑是：先定目标人物，再从
**他们关注的账号**里找共同人选。目标人物是 Elon Musk、Sam Altman、a16z 和
Sequoia 的 GP、Karpathy、LeCun —— **这些人一个都买不到**，他们是客户想被看见
的受众，不是投放对象。能买的是他们关注的那批人。

和已有两个采集脚本的分工
------------------------
* ``collect_follow_graph`` 采**我方创作者**关注了谁。方向是
  ``我们的 KOL → 关注 → 账号``，只能用来发现「我们这个圈子在看谁」。它给出的
  ``roster_centrality`` 反映的是 Mango 签了谁，不是行业地位 —— 实测里它把同圈
  互推的账号排到了前面。
* ``collect_attention_signals`` 采 Root 候选关注了谁，但**只保留与我方名单的
  交集**，完整关注列表没有落库。所以「这些名人还共同关注了哪些我们库外的
  账号」一直算不出来。
* 本脚本采**目标人物**关注了谁，并把**完整关注列表**写进 ``follow_edges``。
  有了它，「被 ≥N 位所选目标人物共同关注」才成为一次实时查询。

方向纪律
--------
这里的边是 ``目标人物 → 关注 → 账号``。这**正是**路径 3 想要的方向：候选的
内容有机会进入这位目标人物的信息流。它仍然只证明观察时存在关注关系，不证明
看过、认可或会转发。

截断即失真
----------
名人关注量差别极大（@gdb 关注 8 个，@pmarca 关注 32,758 个）。被页数截断的
名单里「没有某条边」什么都不能说明，所以 ``--max-pages`` 默认给到 40（20,000
个 id），截断时如实写进 ``coverage_limitation``，下游查询据此拒绝下结论。

用法::

    python -m signal_map.scripts.collect_target_following --dry-run
    python -m signal_map.scripts.collect_target_following --handles @karpathy,@sama
    python -m signal_map.scripts.collect_target_following --from-candidates --limit 10
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
for _path in (REPO_ROOT, REPO_ROOT / "src"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from sqlalchemy import func, select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from signal_map.backend import db as sm_db  # noqa: E402
from signal_map.backend.bd_models import BDCandidate  # noqa: E402
from signal_map.backend.models import AttentionSignal  # noqa: E402
from signal_map.backend.observation_models import FollowEdge, XAccount  # noqa: E402
from signal_map.scripts.collect_follow_graph import (  # noqa: E402
    _load_dotenv,
    _upsert_account,
    collect_one,
)

SOURCE_SYSTEM = "signal_map_target_following"

#: 40 页 = 20,000 个 id。比 roster 那边高得多，理由见模块 docstring。
MAX_PAGES = 40


def target_handles_from_candidates(
    session: Session, limit: int | None, domain: str | None = None
) -> list[str]:
    """已入库、被标记为目标人物的候选。

    只读 ``object_kind == 'target_person'``：谁是目标人物由客户选、由人标，
    脚本不自己认定 —— 把一个名人自动当成目标，和把一个候选自动当成 Root 是
    同一类错误。
    """
    query = (
        select(BDCandidate.handle)
        .where(BDCandidate.object_kind == "target_person", BDCandidate.platform == "X")
        .order_by(BDCandidate.id)
    )
    if domain:
        # 按领域采。不加这个过滤，新建一个 crypto 库就得把 AI 那 62 位重采一遍。
        query = query.where(BDCandidate.domain_group == domain)
    if limit:
        query = query.limit(limit)
    return list(session.scalars(query).all())


def resolve_account(session: Session, client, handle: str, force: bool) -> XAccount | None:
    """把 handle 解析成 ``XAccount``，必要时先拉一次公开资料。

    解析不到就**跳过并报出来**，不建占位账号 —— 占位 rest_id 正是之前让整张
    关系图静默返回全零的原因（handle:xxx 永远匹配不上数字 id）。
    """
    cleaned = handle.lstrip("@")
    account = session.scalar(
        select(XAccount).where(func.lower(XAccount.handle) == cleaned.lower())
    )
    if account is not None and account.rest_id and not account.rest_id.startswith("pending:"):
        # 已经收录过也要补上 target 角色。之前这里直接 return，结果凡是早就
        # 在库里的名人（大部分）都没有被标成目标人物，「哪些账号是目标」这个
        # 查询会静默漏掉他们。
        roles = {r.strip() for r in (account.roles or "").split(",") if r.strip()}
        roles.add("target")
        account.roles = ",".join(sorted(roles))
        return account

    try:
        payload = client.get_user(cleaned, force=force, cache_ttl_seconds=86_400 * 7)
    except Exception as exc:  # noqa: BLE001 -- one bad handle must not end the run
        print(f"  ! {handle}: profile lookup failed ({str(exc)[:80]})")
        return None

    profile = _profile_fields(payload)
    rest_id = profile.pop("rest_id", None)
    if not rest_id:
        print(f"  ! {handle}: no rest_id in response, skipped (no placeholder created)")
        return None
    return _upsert_account(session, rest_id, handle=cleaned, role="target", **profile)


#: 用户对象外面裹着的层。实际路径是
#: ``result → data → user → result``，注意 ``result`` **出现了两次** ——
#: 第一版按固定顺序各剥一层，剥到第二个 result 时这个键已经用掉了，于是永远
#: 停在外层，62 位目标里有 43 位被判成「查不到」。它们其实全都存在。
#: 所以这里是**循环下钻直到不再有包装层**，不是按名单剥一遍。
_WRAPPERS = ("result", "data", "user")

#: 名字和用户名现在在 ``core`` 里，粉丝数在 ``legacy`` 里。两个都要摊平，
#: 而且不能覆盖外层的 ``rest_id``。
_MERGE_KEYS = ("legacy", "core")


def _profile_fields(payload: dict) -> dict:
    """把 Rapid X 的 user 响应压成我们存的那几列。

    取不到就留空 —— 空值在这套系统里是合法答案，但**「查不到这个人」不是**：
    那会让一位目标人物静默地从方法里消失，所以解析失败必须报出来。
    """
    node = payload
    for _ in range(8):  # 有限次数，避免异常响应把这里变成死循环
        if not isinstance(node, dict):
            break
        nested = next(
            (k for k in _WRAPPERS if isinstance(node.get(k), dict)), None
        )
        if nested is None:
            break
        node = node[nested]

    flat = dict(node) if isinstance(node, dict) else {}
    for key in _MERGE_KEYS:
        if isinstance(flat.get(key), dict):
            # 外层的 rest_id 优先：``legacy`` 里的 id 字段是旧格式。
            flat = {**flat[key], **flat}

    return {
        "rest_id": str(
            flat.get("rest_id") or flat.get("id_str") or ""
        ) or None,
        "display_name": flat.get("name"),
        "bio": flat.get("description"),
        "followers": flat.get("followers_count"),
        "following": flat.get("friends_count"),
        "listed_count": flat.get("listed_count"),
        "verified": flat.get("verified") or flat.get("is_blue_verified"),
        "profile_refreshed_at": dt.datetime.now(dt.UTC).replace(tzinfo=None),
    }


def roster_by_rest_id(session: Session) -> dict[str, tuple[int, str | None]]:
    """``{rest_id: (creator_id, handle)}``，我方已有身份记录的账号。"""
    rows = session.execute(
        select(XAccount.rest_id, XAccount.creator_id, XAccount.handle).where(
            XAccount.creator_id.is_not(None)
        )
    ).all()
    return {rest_id: (creator_id, handle) for rest_id, creator_id, handle in rows}


def record_attention_signals(
    session: Session, target: XAccount, snapshot, known: dict[str, tuple[int, str | None]],
) -> int:
    """目标人物关注了我方创作者时，同步写一条 ``AttentionSignal``。

    ``follow_edges`` 存的是原始观察，``AttentionSignal`` 存的是**带证据和限制
    说明的路径 3 记录**，推荐引擎读的是后者。两者不是重复：边可以有几万条，
    信号只在真的落到我方对象身上时才产生。
    """
    edges = session.execute(
        select(FollowEdge.target_id, XAccount.rest_id)
        .join(XAccount, XAccount.id == FollowEdge.target_id)
        .where(FollowEdge.source_id == target.id, FollowEdge.disappeared_at.is_(None))
    ).all()

    stamp = dt.datetime.now(dt.UTC).replace(tzinfo=None)
    added = 0
    for _target_id, rest_id in edges:
        hit = known.get(rest_id)
        if hit is None:
            continue
        creator_id, handle = hit
        existing = session.scalar(
            select(AttentionSignal).where(
                AttentionSignal.source_node == target.rest_id,
                AttentionSignal.target_node == rest_id,
                AttentionSignal.signal_type == "follow",
            )
        )
        if existing is not None:
            # 观察是「按次」计的，不是「按运行次数」计的：缓存命中的重跑不该
            # 把一条关注变成一个「反复出现的模式」。
            existing.collected_at = stamp
            continue
        session.add(AttentionSignal(
            source_node=target.rest_id,
            target_node=rest_id,
            source_creator_id=creator_id,
            platform="X",
            signal_type="follow",
            direction="source_to_target",
            observation_count=1,
            raw_evidence=f"@{target.handle} follows @{handle}",
            evidence_url=f"https://x.com/{target.handle}/following",
            collected_at=stamp,
            coverage_limitation=snapshot.coverage_limitation,
            confidence="research_lead",
            human_confirmed=False,
            source_system=SOURCE_SYSTEM,
        ))
        added += 1
    return added


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--handles", help="comma-separated X handles to collect")
    parser.add_argument(
        "--from-candidates", action="store_true",
        help="collect every BDCandidate marked object_kind=target_person",
    )
    parser.add_argument("--limit", type=int, help="cap how many targets this run")
    parser.add_argument("--domain", help="only targets in this domain_group")
    parser.add_argument("--max-pages", type=int, default=MAX_PAGES)
    parser.add_argument("--dry-run", action="store_true", help="list targets only")
    parser.add_argument("--force", action="store_true", help="bypass the response cache")
    args = parser.parse_args()

    _load_dotenv()
    session = sm_db.get_session()
    stats: Counter = Counter()
    try:
        if args.handles:
            handles = [h.strip() for h in args.handles.split(",") if h.strip()]
        elif args.from_candidates:
            handles = target_handles_from_candidates(session, args.limit, args.domain)
        else:
            parser.error("pass --handles or --from-candidates")

        if args.limit:
            handles = handles[: args.limit]
        print(f"targets: {len(handles)}")
        if args.dry_run:
            for handle in handles:
                print(f"  @{handle.lstrip('@')}")
            print("\n[DRY RUN] nothing collected")
            return

        from mangobd.rapid_x import RapidXClient

        client = RapidXClient()
        known = roster_by_rest_id(session)

        for index, handle in enumerate(handles, 1):
            account = resolve_account(session, client, handle, args.force)
            if account is None:
                stats["unresolved"] += 1
                continue
            session.commit()

            snapshot = collect_one(
                session, client, account, account.handle or handle, args.force,
                max_pages=args.max_pages,
            )
            signals = record_attention_signals(session, account, snapshot, known)
            session.commit()

            stats["collected"] += 1
            stats["edges"] += snapshot.edge_count
            stats["attention_signals"] += signals
            if not snapshot.is_complete:
                stats["truncated"] += 1
            flag = "" if snapshot.is_complete else "  [TRUNCATED]"
            print(
                f"  [{index}/{len(handles)}] @{account.handle}: "
                f"{snapshot.edge_count} following, {signals} new signal(s){flag}"
            )
    finally:
        session.close()

    print("\n--- summary ---")
    for key, value in sorted(stats.items()):
        print(f"  {key:22} {value}")
    if stats.get("truncated"):
        print(
            f"\n注意：{stats['truncated']} 位目标人物的关注列表被页数截断。"
            "截断名单里「没有某条边」不能读成「没有关注」。"
        )


if __name__ == "__main__":
    main()
