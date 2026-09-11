"""补采「被名人共同关注」账号的公开资料。

采完 62 位目标人物的关注列表后，得到 22,309 个被 ≥2 位共同关注的账号 —— 但其中
只有 427 个有简介。``follow_edges`` 只记录了 rest_id，没有任何资料，而**没有简介
就没有任何内容依据**：领域判不出来、商业信号读不到、卡片上除了粉丝数什么都写不出。
那样的候选进队列只会让人逐个点开去看，等于把筛选推回给人做。

所以这一步按**共同关注人数**从高到低补采资料。共同关注人数就是发现信号本身，
拿它当采集优先级是自然的：被 45 位名人共同关注的账号，值得先花一次 API 调用。

批量端点一次能解析多个 id，所以这一步很便宜 —— 1,700 个账号约 17 次调用。

用法::

    python -m signal_map.scripts.enrich_cofollowed_profiles --dry-run
    python -m signal_map.scripts.enrich_cofollowed_profiles --min-targets 10
    python -m signal_map.scripts.enrich_cofollowed_profiles --min-targets 5 --limit 2000
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

from sqlalchemy import select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from signal_map.backend import bd_discovery, db as sm_db  # noqa: E402
from signal_map.backend.bd_models import BDCandidate  # noqa: E402
from signal_map.backend.observation_models import XAccount  # noqa: E402
from signal_map.scripts.collect_follow_graph import _load_dotenv  # noqa: E402

#: 一次批量解析多少个 id。保守取值：端点没有文档化上限，一次失败会让整批
#: 白跑，而多跑几次调用比丢一批便宜。
BATCH = 100


def target_account_ids(session: Session) -> list[int]:
    """已入库、被标记为目标人物且已收录 X 账号的那些人。"""
    handles = set(
        session.scalars(
            select(BDCandidate.handle).where(
                BDCandidate.object_kind == "target_person", BDCandidate.platform == "X"
            )
        ).all()
    )
    if not handles:
        return []
    return [
        account.id
        for account in session.scalars(select(XAccount).where(XAccount.handle.is_not(None)))
        if (account.handle or "").lower() in handles
    ]


def _apply(account: XAccount, row: dict) -> bool:
    """把一条批量响应写进账号。返回是否真的变了。

    数值字段在这个端点里是**字符串**（``"4123720"``）。直接存进 Integer 列，
    SQLite 会照单全收，之后所有按粉丝量的排序都会变成字典序 —— 一个不报错、
    只是顺序悄悄错掉的问题。
    """
    def as_int(value):
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    before = (account.bio, account.followers, account.following, account.listed_count)
    account.display_name = row.get("name") or account.display_name
    account.bio = row.get("description") or account.bio
    account.followers = as_int(row.get("followers_count")) or account.followers
    account.following = as_int(row.get("friends_count")) or account.following
    account.listed_count = as_int(row.get("listed_count")) or account.listed_count
    if account.verified is None:
        account.verified = row.get("verified") or row.get("is_blue_verified")

    handle = row.get("screen_name")
    if handle and not account.handle:
        account.handle = handle
    account.profile_refreshed_at = dt.datetime.now(dt.UTC).replace(tzinfo=None)
    return before != (account.bio, account.followers, account.following, account.listed_count)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--min-targets", type=int, default=10,
        help="only enrich accounts followed by at least this many targets",
    )
    parser.add_argument("--limit", type=int, help="cap how many accounts this run")
    parser.add_argument(
        "--frontrun", action="store_true",
        help="改为补齐 FrontRun 新关注里尚未拉过资料的账号",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true", help="bypass the response cache")
    args = parser.parse_args()

    _load_dotenv()
    session = sm_db.get_session()
    stats: Counter = Counter()
    try:
        target_ids = target_account_ids(session)
        if not target_ids:
            print("no target people in the database; run collect_target_following first")
            return

        if args.frontrun:
            from signal_map.backend import frontrun

            ids = frontrun.report(session).unresolved
            rows = [
                (account, [])
                for account in session.scalars(
                    select(XAccount).where(XAccount.rest_id.in_(ids))
                )
            ] if ids else []
        else:
            rows = bd_discovery.target_followed_accounts(
                session, target_ids, min_targets=args.min_targets
            )
        # 已经有简介的跳过：这一步是补空白，不是刷新。
        pending = [(account, edges) for account, edges in rows if not account.bio]
        if args.limit:
            pending = pending[: args.limit]

        print(
            f"被 >= {args.min_targets} 位目标人物共同关注：{len(rows)} 个账号，"
            f"其中 {len(pending)} 个缺公开资料"
        )
        if args.dry_run:
            for account, edges in pending[:20]:
                print(f"  {len(edges):3} 位 | {account.rest_id} @{account.handle or '?'}")
            print("\n[DRY RUN] nothing collected")
            return
        if not pending:
            print("nothing to enrich")
            return

        from mangobd.rapid_x import RapidXClient

        client = RapidXClient()
        by_rest_id = {a.rest_id: a for a, _ in pending}
        ids = list(by_rest_id)

        for start in range(0, len(ids), BATCH):
            chunk = ids[start : start + BATCH]
            try:
                payload = client.get_users_by_ids(
                    chunk, force=args.force, cache_ttl_seconds=86_400 * 7
                )
            except Exception as exc:  # noqa: BLE001 -- one bad batch must not end the run
                stats["batch_errors"] += 1
                print(f"  ! batch {start // BATCH + 1} failed: {str(exc)[:80]}")
                continue

            returned = payload.get("result") or []
            for row in returned:
                account = by_rest_id.get(str(row.get("id_str") or row.get("id") or ""))
                if account is None:
                    continue
                if _apply(account, row):
                    stats["updated"] += 1
                else:
                    stats["unchanged"] += 1
            # 请求了 N 个、回来 M 个，差额是**读不到的账号**（销号、改名、私密）。
            # 它们保持空白并被计数，不猜、也不静默跳过。
            stats["requested"] += len(chunk)
            stats["returned"] += len(returned)
            session.commit()
            print(
                f"  [{min(start + BATCH, len(ids))}/{len(ids)}] "
                f"{len(returned)}/{len(chunk)} resolved"
            )
    finally:
        session.close()

    print("\n--- summary ---")
    for key, value in sorted(stats.items()):
        print(f"  {key:22} {value}")
    missing = stats.get("requested", 0) - stats.get("returned", 0)
    if missing > 0:
        print(f"\n{missing} 个账号读不到公开资料，保持待确认，不作任何推断。")


if __name__ == "__main__":
    main()
