"""判断被目标人物共同关注的账号**卖不卖内容位**，写成建议。

为什么需要这一步
----------------
@HesterPeirce（SEC 委员）出现在了客户的可投放候选里。在这之前我用简介关键词
清过好几轮 —— CEO、记者、会议号、feedback 邮箱、职务信箱 —— 每一轮清掉一批，
下一批数据换个形态又冒出来。关键词分不清「有影响力」和「售卖内容位」，因为这
两件事在简介里长得一模一样：一个人 + 一份履历。

护栏（CLAUDE.md §3）
--------------------
* 输出按闭集 ``ACCOUNT_KINDS`` 校验，**每个判断必须引用原文**，没引用降级为
  ``unclear``。
* ``unclear`` 是正常且频繁的正确答案，**永不覆盖已有的人工判定**。
* 写进 ``object_kind_suggested``，不是 ``object_kind`` —— 建议和决定分列。
* 缓存键含 prompt 指纹：改了 prompt，旧答案就不该复用。

为什么客户面可以读这条建议
--------------------------
一般规则是「机器建议不参与判断」。这里破例，但只在**一个方向**上：建议只用来
**把人从可投放名单里拿掉**，永远不用来把人放进去。两个方向的代价不对称 ——
错误排除一个可买的人，损失一次机会；错误放进一个 SEC 委员，是产品级别的尴尬。
建议本身照常进内部队列等人确认或推翻。

用法::

    python -m signal_map.scripts.classify_account_roles --dry-run
    python -m signal_map.scripts.classify_account_roles --min-targets 4
    python -m signal_map.scripts.classify_account_roles --handles HesterPeirce,RAC
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
for _path in (REPO_ROOT, REPO_ROOT / "src"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from sqlalchemy import func, select  # noqa: E402
from sqlalchemy.exc import IntegrityError  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from signal_map.backend import bd_discovery, db as sm_db  # noqa: E402
from signal_map.backend.bd_models import BDCandidate  # noqa: E402
from signal_map.backend.llm_classify import (  # noqa: E402
    LLMError,
    build_profile_text,
    classify_account,
    classify_verticals,
    resolve_provider,
)
from signal_map.backend.observation_models import XAccount  # noqa: E402
from signal_map.scripts.collect_follow_graph import _load_dotenv  # noqa: E402

SUGGESTED_BY = "llm_account_role"


def accounts_to_classify(
    session: Session, *, min_targets: int, handles: list[str] | None,
) -> list[XAccount]:
    """要判的账号：**能进客户名单的那些**，不是全库。

    全库 28 万个账号，绝大多数永远不会被任何客户看到。按「被几位目标人物关注」
    收口，是因为那正好是进入客户名单的门槛。
    """
    if handles:
        wanted = {h.strip().lstrip("@").lower() for h in handles if h.strip()}
        return [
            account
            for account in session.scalars(select(XAccount).where(XAccount.handle.is_not(None)))
            if (account.handle or "").lower() in wanted
        ]

    target_ids = [
        account.id
        for account in session.scalars(select(XAccount).where(XAccount.handle.is_not(None)))
        if (account.handle or "").lower() in _target_handles(session)
    ]
    rows = bd_discovery.target_followed_accounts(
        session, target_ids, min_targets=min_targets
    )
    # 没简介就没有可判的内容。判空是浪费一次调用，而且模型只会回 unclear。
    return [account for account, _edges in rows if (account.bio or "").strip()]


def already_classified(session: Session) -> set[str]:
    """已经有建议的 handle。

    这是**断点续跑**用的。全量扫描要跑几千次调用，中途被杀掉是常态（第一次
    就在 3,934/7,018 处被外部终止了）。LLM 缓存能省掉重复的 API 开销，但仍然
    要为每个账号重新组装 profile、读缓存、写库；按 handle 跳过让重启接近瞬时。

    ``--force`` 会绕过它 —— 改了 prompt 就该全部重判。
    """
    return {
        (handle or "").lower()
        for handle in session.scalars(
            # 按「判过了」跳，不是按「判出结果了」跳 —— 见 upsert_suggestion。
            select(BDCandidate.handle).where(
                BDCandidate.object_kind_suggested_by.is_not(None)
            )
        ).all()
        if handle
    }


def _target_handles(session: Session) -> set[str]:
    return {
        (h or "").lower()
        for h in session.scalars(
            select(BDCandidate.handle).where(BDCandidate.object_kind == "target_person")
        ).all()
        if h
    }


def upsert_suggestion(session: Session, account: XAccount, verdict) -> str:
    """把建议写进候选行。人工判定过的行不覆盖。

    返回 ``written`` / ``skipped_human`` / ``skipped_unclear``。
    """
    handle = (account.handle or account.rest_id).lower()
    candidate = session.scalar(
        select(BDCandidate).where(
            BDCandidate.platform == "X", func.lower(BDCandidate.handle) == handle
        )
    )
    if candidate is None:
        candidate = BDCandidate(
            platform="X", handle=handle, display_name=account.display_name,
            profile_url=f"https://x.com/{account.handle}" if account.handle else None,
            source_name=SUGGESTED_BY,
        )
        session.add(candidate)
        # 立刻 flush。不 flush 的话，这条 INSERT 会一直挂着，直到下一次
        # ``select`` 触发 autoflush 才写出去 —— 那时报的 UNIQUE 冲突指向的是
        # **另一个账号的查询语句**，堆栈完全对不上出问题的那一行。
        try:
            session.flush()
        except IntegrityError:
            session.rollback()
            candidate = session.scalar(
                select(BDCandidate).where(
                    BDCandidate.platform == "X",
                    func.lower(BDCandidate.handle) == handle,
                )
            )
            if candidate is None:
                return "skipped_conflict"

    if candidate.object_kind and candidate.object_kind != "unknown":
        # 人已经判过了。建议仍然记下来 —— 两者不一致本身就值得复核看一眼。
        candidate.object_kind_suggested = _map_kind(verdict.kind)
        candidate.object_kind_suggestion_basis = verdict.basis
        candidate.object_kind_suggested_by = f"{SUGGESTED_BY}:{verdict.model}"
        return "skipped_human"

    if verdict.kind == "unclear":
        # 空是合法答案，**永不覆盖已有值**。但要记下「判过了」—— 否则每次
        # 重跑都会把这批薄简介账号重新走一遍，全量扫描永远收敛不了（实测每轮
        # 800 个里约 715 个是它们）。简介变了缓存自然会失效并重判。
        candidate.object_kind_suggestion_basis = verdict.basis
        candidate.object_kind_suggested_by = f"{SUGGESTED_BY}:{verdict.model}"
        return "skipped_unclear"

    candidate.object_kind_suggested = _map_kind(verdict.kind)
    candidate.object_kind_suggestion_basis = verdict.basis
    candidate.object_kind_suggested_by = f"{SUGGESTED_BY}:{verdict.model}"
    return "written"


def add_verticals(session: Session, account: XAccount, candidate: BDCandidate) -> bool:
    """给可投放的账号补一次领域判定。

    只对 ``kol`` / ``media_channel`` 跑：领域是用来**按项目筛选**的，而买不到
    的人根本不进名单，判他们的领域没有用处。

    这一步解决的是一个具体故障：简介关键词只对约一半的候选推得出领域，剩下
    那一半 ``domains`` 为空，于是**任何领域过滤都拦不住他们** —— 一个讲邻里
    八卦的账号（@bestofnextdoor）因为被科技名人关注，出现在了 AI 项目的名单里。
    """
    if candidate.verticals_suggested:
        return False
    text = build_profile_text(display_name=account.display_name, bio=account.bio)
    try:
        verdict = classify_verticals(text)
    except LLMError:
        return False
    if not verdict.keys:
        return False
    candidate.verticals_suggested = verdict.csv
    candidate.verticals_suggestion_basis = (
        f"模型判定领域（{verdict.confidence}）"
        + (f"；依据原文：{verdict.evidence_text}" if verdict.evidence_text else "")
    )
    return True


#: 模型词表 -> 产品词表。两套分开是有意的：模型答的是「卖不卖内容位」，产品
#: 里的 ``target_person`` 还多一层「客户想不想触达」，那不是模型能判的。
_KIND_MAP = {
    "creator": "kol",
    "media": "media_channel",
    "public_figure": "public_figure",
    "organization": "organization",
}


def _map_kind(kind: str) -> str:
    return _KIND_MAP.get(kind, "unknown")


def _verticals_pass(session: Session, accounts, stats: Counter) -> None:
    """只给「可投放且还没有领域」的账号跑一次领域判定。"""
    by_handle = {
        (c.handle or "").lower(): c
        for c in session.scalars(select(BDCandidate))
    }
    pending = [
        (account, candidate)
        for account in accounts
        if (candidate := by_handle.get((account.handle or "").lower())) is not None
        and candidate.effective_object_kind in ("kol", "media_channel")
        and not candidate.verticals_suggested
    ]
    print(f"需要补领域的可投放账号：{len(pending)}")
    for index, (account, candidate) in enumerate(pending, 1):
        if add_verticals(session, account, candidate):
            stats["verticals_added"] += 1
            print(
                f"  [{index}/{len(pending)}] @{account.handle:20} "
                f"{candidate.verticals_suggested}"
            )
        else:
            stats["verticals_none"] += 1
        if index % 20 == 0:
            session.commit()
    session.commit()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--min-targets", type=int, default=4)
    parser.add_argument("--handles", help="comma-separated handles instead of a sweep")
    parser.add_argument("--limit", type=int)
    parser.add_argument(
        "--verticals-only", action="store_true",
        help="只给已判定为可投放、但还没有领域的账号补领域",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true", help="bypass the verdict cache")
    args = parser.parse_args()

    _load_dotenv()
    session = sm_db.get_session()
    stats: Counter = Counter()
    try:
        handles = args.handles.split(",") if args.handles else None
        accounts = accounts_to_classify(
            session, min_targets=args.min_targets, handles=handles
        )
        if not args.force and not handles and not args.verticals_only:
            done = already_classified(session)
            before = len(accounts)
            accounts = [a for a in accounts if (a.handle or "").lower() not in done]
            if before != len(accounts):
                print(f"跳过已判定：{before - len(accounts)}（--force 可重判）")
        if args.limit:
            accounts = accounts[: args.limit]
        print(f"待判账号：{len(accounts)}")

        if args.dry_run:
            for account in accounts[:20]:
                print(f"  @{account.handle:22} {(account.bio or '')[:60]}")
            print("\n[DRY RUN] 未调用模型，也未写入")
            return

        print(f"provider: {resolve_provider().model}\n")
        if args.verticals_only:
            # 身份已经判过了，这一步只补领域 —— 重跑身份判定是纯浪费。
            # 早返回会跳过下面的汇总打印，所以这里不 return，用一个标志走完。
            _verticals_pass(session, accounts, stats)
            accounts = []
        for index, account in enumerate(accounts, 1):
            text = build_profile_text(
                display_name=account.display_name, bio=account.bio,
            )
            try:
                verdict = classify_account(text, force=args.force)
            except LLMError as exc:
                stats["errors"] += 1
                print(f"  ! @{account.handle}: {str(exc)[:70]}")
                continue

            outcome = upsert_suggestion(session, account, verdict)
            stats[outcome] += 1
            if verdict.kind in ("creator", "media"):
                candidate = session.scalar(
                    select(BDCandidate).where(
                        BDCandidate.platform == "X",
                        func.lower(BDCandidate.handle)
                        == (account.handle or account.rest_id).lower(),
                    )
                )
                if candidate is not None and add_verticals(session, account, candidate):
                    stats["verticals_added"] += 1
            stats[f"kind:{verdict.kind}"] += 1
            if verdict.from_cache:
                stats["from_cache"] += 1
            if verdict.kind != "unclear":
                mark = "✗ 买不到" if verdict.buyable is False else "✓ 可投放"
                print(
                    f"  [{index}/{len(accounts)}] @{account.handle:22} "
                    f"{verdict.kind:14} {mark} | {(verdict.evidence or '')[:44]}"
                )
            if index % 25 == 0:
                session.commit()
        session.commit()
    finally:
        session.close()

    print("\n--- summary ---")
    for key, value in sorted(stats.items()):
        print(f"  {key:24} {value}")
    print(
        "\n这些是**建议**，不是判定。人工确认走 "
        "PATCH /api/internal/bd/candidates/X/{handle} 写 object_kind。"
    )


if __name__ == "__main__":
    main()
